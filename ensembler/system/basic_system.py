"""
Module: System
    This module shall be used to implement subclasses of system1D. It wraps all information needed and generated by a simulation.
"""

import os, numpy as np
from tqdm import tqdm
from typing import Iterable, NoReturn
from numbers import Number
import pandas as pd
import scipy.constants as const
import warnings
pd.options.mode.use_inf_as_na = True


from ensembler.util import dataStructure as data
from ensembler.potentials.ND import envelopedPotential
from ensembler.potentials._baseclasses import _potentialNDCls as _potentialCls
from ensembler.potentials._baseclasses import _perturbedPotentialNDCls as _perturbedPotentialCls

from ensembler.integrator import _integratorCls,newtonianIntegrator
from ensembler.conditions._conditions import Condition

class system:
    """
     [summary]
    
    :raises IOError: [description]
    :raises Exception: [description]
    :return: [description]
    :rtype: [type]
    """
    #static attributes
    state = data.basicState

    def __init__(self, potential:_potentialCls, integrator:_integratorCls, conditions:Iterable[Condition]=[],
                 temperature:Number=298.0, position:(Iterable[Number] or Number)=None, mass:Number=1, verbose:bool=True)->NoReturn:
        ################################
        # Declare Attributes
        #################################
        
        ##essential parts
        self.potential: _potentialCls = None
        self.integrator: _integratorCls = None
        self.conditions: Iterable[Condition] = []

        ##Physical parameters
        self.temperature: float = 298.0
        self.mass: float = 1  # for one particle systems!!!!
        self.nparticles: int = 1  # Todo: adapt it to be multiple particles

        self.nDim: int = -1
        self.nStates: int = 1

        # Output
        self.initial_position: Iterable[float] or float

        self.currentState: data.basicState = data.basicState(np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan)
        self.trajectory: pd.DataFrame = pd.DataFrame(columns=list(self.state.__dict__["_fields"]))

        # tmpvars - private:
        self._currentTotE: (Number) = np.nan
        self._currentTotPot: (Number) = np.nan
        self._currentTotKin: (Number) = np.nan
        self._currentPosition: (Number or Iterable[Number]) = np.nan
        self._currentVelocities: (Number or Iterable[Number]) = np.nan
        self._currentForce: (Number or Iterable[Number]) = np.nan
        self._currentTemperature: (Number or Iterable[Number]) = np.nan


        #BUILD System
        ## Fundamental Parts:
        self.potential = potential
        self.integrator = integrator
        self.conditions = conditions

        ## set dim
        if(potential.nDim < 1 and isinstance(position, Iterable) and all([isinstance(pos, Number) for pos in position])):  #one  state system.
            self.nDim = len(position)
            self.potential.nDim = self.nDim
        elif(potential.nDim > 0):
            self.nDim = potential.nDim
        else:
            raise IOError("Could not estimate the disered Dimensionality as potential dim was <1 and no initial position was given.")
        self.temperature = temperature
        self.mass = mass

        ###is the potential a state dependent one? - needed for initial pos.
        if(hasattr(potential, "nStates")):
            self.nStates = potential.nStates
            if(hasattr(potential, "states_coupled")):   #does each state get the same position?
                self.states_coupled = potential.states_coupled
            else:
                self.states_coupled = True #Todo: is this a good Idea?
        else:
            self.nstates = 1

        #PREPARE THE SYSTEM
        ##Make System Potential and initial State
        self.init_position(initial_position=position)
        ##do we need velocities?
        if(issubclass(integrator.__class__, newtonianIntegrator)):
            self.init_velocities()

        ##check if system should be coupled to conditions:
        for condition in self.conditions:
            if(not hasattr(condition, "system")):
                condition.coupleSystem(self)
            else:
                #warnings.warn("Decoupling system and coupling it again!")
                condition.coupleSystem(self)
            if(not hasattr(condition, "dt") and hasattr(self.integrator, "dt")):
                condition.dt = self.integrator.dt
            else:
                condition.dt=1

        self.verbose = verbose

    """
        Initialisation
    """
    def initialise(self, withdraw_Traj:bool=False, init_position:bool=True, init_velocity:bool=True):
        if(withdraw_Traj):
            self.trajectory = pd.DataFrame(columns=list(self.state.__dict__["_fields"]))

        if(init_position):
            self.init_position()

        #Try to init the force
        try:
            self._currentForce = self.potential.dhdpos(self.initial_position)  #initialise forces!    #todo!
        except:
            warnings.warn("Could not initialize the force of the potential? Check if you need it!")

        if(init_velocity):
            self.init_velocities()

        # set initial Temperature
        self._currentTemperature = self.temperature

        #update current state
        self.updateEne()
        self.updateCurrentState()

        self.trajectory = self.trajectory.append(self.currentState._asdict(), ignore_index=True)

    def init_position(self, initial_position=None):

        #Set initial position
        if (type(initial_position) == type(None)):
            self.initial_position = self.randomPos()
        elif(isinstance(initial_position, Iterable)):
            if(len(initial_position) == 1 and  len(np.array(initial_position.shape))==1):
                self.initial_position=np.array(self.initial_position).item()
            else:
                self.initial_position = initial_position
        elif(isinstance(initial_position, Number)):
            self.initial_position = initial_position
        else:
            raise Exception("did not understand the initial position!")

        self._currentPosition = self.initial_position

        return self.initial_position

    def init_velocities(self)-> NoReturn:
        if(self.nStates>1):
            self._currentVelocities = [[self._gen_rand_vel() for dim in range(self.nDim)] for s in range(self.nStates)] if(self.nDim>1) else [self._gen_rand_vel() for state in range(self.nStates)]
        else:
            self._currentVelocities = [self._gen_rand_vel() for dim in range(self.nDim)] if (self.nDim > 1) else self._gen_rand_vel()

        self.veltemp = self.mass / const.gas_constant / 1000.0 * np.linalg.norm(self._currentVelocities) ** 2  # t

        self.updateEne()
        self.set_current_state(currentPosition=self._currentPosition, currentVelocities=self._currentVelocities, currentForce=self._currentForce, currentTemperature=self.temperature)
        return self._currentVelocities

    def _gen_rand_vel(self)->float:
        return np.sqrt(const.gas_constant / 1000.0 * self.temperature / self.mass) * np.random.normal()

    def randomPos(self)-> Iterable:
        print("SYSTEM ASSIGNS random POSITION FOR: states: "+str(self.nStates)+"\tnDim: "+str(self.nDim))
        if(self.nStates > 1):    #TODO: remains to be tested!
            return [np.subtract(np.multiply(np.random.rand(self.nDim),20),10) for state in range(self.nStates)]
        else:
            random_pos = np.squeeze(np.array([np.subtract(np.multiply(np.random.rand(self.nDim), 20), 10) for state in range(self.nStates)]))
            if(len(random_pos.shape) == 1 and random_pos.shape[0] == 1):
                random_pos = random_pos.item()

            return random_pos

    """
        Update
    """
    def totKin(self)-> (Iterable[Number] or Number or None):
        # Todo: more efficient if?
        if(self.nDim == 1 and isinstance(self._currentVelocities, Number) and not np.isnan(self._currentVelocities)):
            return 0.5 * self.mass * np.square(np.linalg.norm(self._currentVelocities))
        elif(self.nDim > 1 and isinstance(self._currentVelocities, Iterable) and all([isinstance(x, Number) and not np.isnan(x) for x in self._currentVelocities])):
            return np.sum(0.5 * self.mass * np.square(np.linalg.norm(self._currentVelocities)))
        else:
            return np.nan

    def totPot(self)-> (Iterable[Number] or Number or None):
        return self.potential.ene(self._currentPosition)

    def updateTemp(self)-> NoReturn:
        """ this looks like a thermostat like thing! not implemented!@ TODO calc velocity from speed"""
        self._currentTemperature = self._currentTemperature

    def updateEne(self)-> NoReturn:
        self._currentTotPot = self.totPot()
        self._currentTotKin = self.totKin()
        self._currentTotE = self._currentTotPot if(np.isnan(self._currentTotKin))else np.add(self._currentTotKin, self._currentTotPot)

    def updateCurrentState(self)-> NoReturn:
        self.currentState = self.state(self._currentPosition, self._currentTemperature,
                                        self._currentTotE, self._currentTotPot, self._currentTotKin,
                                        self._currentForce, self._currentVelocities)

    def _update_current_vars(self):
        self._currentPosition = self.currentState.position
        self._currentTemperature = self.currentState.temperature
        self._currentTotE = self.currentState.totEnergy
        self._currentTotPot = self.currentState.totPotEnergy
        self._currentTotKin = self.state.totKinEnergy
        self._currentForce = self.currentState.dhdpos
        self._currentVelocities = self.currentState.velocity

    """
        Functionality
    """

    def simulate(self, steps:int,  withdrawTraj:bool=False, save_every_state:int=1, initSystem:bool=False,)-> state:
        
        if(steps > 1000 and self.verbose):
            show_progress =True
            block_length = steps*0.1
        else:
            show_progress = False

        if(withdrawTraj):
            self.trajectory: pd.DataFrame = pd.DataFrame(columns=list(self.state.__dict__["_fields"]))
            self.trajectory = self.trajectory.append(self.currentState._asdict(), ignore_index=True)

        if(initSystem): #type(self._currentVelocities) == type(None) or type(self._currentPosition) == type(None)
            self.init_velocities()
            self.init_position(initial_position=self.initial_position)

        self.updateCurrentState()
        self.updateEne()
        
        #if(show_progress): print("Progress: ", end="\t")
        
        #self.potential.set_simulation_mode() #TODO FIX
        step = 0
        for step in tqdm(range(steps), desc="Simulation: ", mininterval=1.0, disable=show_progress):
            #if(show_progress and step%block_length==0):
                #print(str(100*step//steps)+"%", end="\t")

            #Do one simulation Step. Todo: change to do multi steps
            self.propergate()

            #Calc new Energy
            self.updateEne()

            #Apply Restraints, Constraints ...
            self.applyConditions()

            #Set new State
            self.updateCurrentState()

            if(step%save_every_state == 0 ):
                self.trajectory = self.trajectory.append(self.currentState._asdict(), ignore_index=True)

        if(step%save_every_state != 0 ):
            self.trajectory.append(self.currentState)
        self.potential.set_simulation_mode(False)

        #if(show_progress): print("100%")
        return self.currentState

    def propergate(self)->NoReturn:
        self._currentPosition, self._currentVelocities, self._currentForce = self.integrator.step(self)

    def applyConditions(self)-> NoReturn:
        for aditional in self.conditions:
            #setattr(aditional, "system", self)  #todo: nicer solution?
            aditional.apply()

    def append_state(self, newPosition, newVelocity, newForces)->NoReturn:
        self._currentPosition = newPosition
        self._currentVelocities = newVelocity
        self._currentForce = newForces

        self.updateTemp()
        self.updateEne()
        self.updateCurrentState()

        self.trajectory = self.trajectory.append(self.currentState._asdict(), ignore_index=True)

    def revertStep(self)-> NoReturn:
        self.currentState = self.trajectory[-2]
        self._update_current_vars()
        return

    """
        Getter
    """
    def getTotPot(self)-> (Iterable[Number] or Number or None):
        return self._currentTotPot

    def getTotEnergy(self)-> (Iterable[Number] or Number or None):
        return self._currentTotE

    def getCurrentState(self)->state:
        return self.currentState
    
    def getTrajectoryObjects(self)->Iterable[state]:
        return self.trajectory

    def getTrajectory(self)->pd.DataFrame:
        return pd.DataFrame.from_dict([frame._asdict() for frame  in self.trajectory])

    def writeTrajectory(self, out_path:str)->str:
        if(not os.path.exists(os.path.dirname(out_path))):
            raise Exception("Could not find output folder: "+os.path.dirname(out_path))
        df = pd.DataFrame.from_dict([frame._asdict() for frame  in self.trajectory])
        df.to_csv(out_path, header=True)
        del df
        return out_path


    def set_position(self, position):
        self._currentPosition = position
        if(len(self.trajectory) == 0):
            self.initial_position = self._currentPosition
        self.updateEne()
        self.updateCurrentState()

    def set_velocities(self, velocities):
        self._currentVelocities = position
        self.updateEne()
        self.updateCurrentState()

    def set_current_state(self, currentPosition:(Number or Iterable), currentVelocities:(Number or Iterable)=0, currentForce:(Number or Iterable)=0, currentTemperature:Number=298):
        self._currentPosition = currentPosition
        self._currentForce = currentForce
        self._currentVelocities = currentVelocities
        self._currentTemperature = currentTemperature
        self.currentState = self.state(self._currentPosition, self._currentTemperature, np.nan, np.nan, np.nan, np.nan, np.nan)

        self.updateEne()
        self.updateCurrentState()

    def set_Temperature(self, temperature):
        """ this looks like a thermostat like thing! not implemented!@"""
        self.temperature = temperature
        self._currentTemperature = temperature
        self.updateEne()