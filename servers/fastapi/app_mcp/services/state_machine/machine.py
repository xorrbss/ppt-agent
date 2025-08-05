from typing import Dict, Set, Any
from app_mcp.services.state_machine.context import StateContext
from app_mcp.services.state_machine.states import PresentationState
from app_mcp.services.state_machine.constants import TRANSITIONS, SUGGESTIONS, PROGRESS_WEIGHTS, ERROR_STATES

class PresentationStateMachine:
    def __init__(self):
        self.state = PresentationState.INIT
        self.context = StateContext()
        self._state_history = [PresentationState.INIT]
        self._transitions = TRANSITIONS
        self._error_states = ERROR_STATES
        self._suggestions = SUGGESTIONS
        self._progress_weights = PROGRESS_WEIGHTS


    def transition(self, new_state: PresentationState, context_updates: Dict[str, Any] = None):
        """
        Transition to new state with optional context updates
        Args:
            new_state (PresentationState): The state to transition to
            context_updates (Dict[str, Any], optional): Context data to update during transition
        Raises:
            ValueError: If the transition is not valid
        """
        if not self.is_valid_transition(new_state):
            raise ValueError(f"Invalid transition from {self.state} to {new_state}")
        
        # Update context if provided
        if context_updates:
            for key, value in context_updates.items():
                if hasattr(self.context, key):
                    setattr(self.context, key, value)
                else:
                    self.context.metadata[key] = value

        # Record state history
        self._state_history.append(new_state)
        self.state = new_state

    def is_valid_transition(self, new_state: PresentationState) -> bool:
        """Check if transition to new state is valid"""
        return new_state in self._transitions.get(self.state, set())

    def get_available_transitions(self) -> Set[PresentationState]:
        """Get all valid transitions from current state"""
        return self._transitions.get(self.state, set())

    def can_transition_to(self, target_state: PresentationState) -> bool:
        """Check if can transition to target state"""
        return target_state in self.get_available_transitions()

    def is_terminal_state(self) -> bool:
        """Check if current state is terminal (no outgoing transitions)"""
        return len(self.get_available_transitions()) == 0

    def is_error_state(self) -> bool:
        """Check if current state is an error state"""
        return self.state in self._error_states

    def get_workflow_progress(self) -> float:   
        """Calculate workflow progress as percentage"""
        return self._progress_weights.get(self.state, 0)

    def get_next_suggested_action(self) -> str:
        """Get suggested next action based on current state"""
        return self._suggestions.get(self.state, "No suggestions available")

    def reset(self):
        """Reset state machine to initial state"""
        self.state = PresentationState.INIT
        self.context = StateContext()
        self._state_history = [PresentationState.INIT]

    def get_state_history(self) -> list:
        """Get history of states visited"""
        return self._state_history.copy()

    def rollback_to_previous_state(self) -> bool:
        """Rollback to previous state if possible"""
        if len(self._state_history) < 2:
            return False

        # Remove current state from history
        self._state_history.pop()
        previous_state = self._state_history[-1]

        if self.is_valid_transition(previous_state):
            self.state = previous_state
            return True
        else:
            self._state_history.append(self.state)
            return False

    def __str__(self):
        return f"PresentationStateMachine(state={self.state.name}, progress={self.get_workflow_progress()}%)"

    def __repr__(self):
        return (f"PresentationStateMachine(state={self.state.name}, "
                f"context={self.context}, "
                f"history_length={len(self._state_history)})")
