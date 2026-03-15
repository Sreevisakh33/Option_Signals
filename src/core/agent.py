from abc import ABC, abstractmethod

class BaseAgent(ABC):
    """
    Abstract base class for all Trading Agents.
    Forces implementation of a basic execution pipeline.
    """
    
    @abstractmethod
    def acquire_data(self) -> tuple:
        """Fetch all required market data and visuals."""
        pass
        
    @abstractmethod
    def process_data(self, *args, **kwargs) -> str:
        """Process raw data into a structured context window snippet."""
        pass
        
    @abstractmethod
    def run_strategy(self, *args, **kwargs) -> str:
        """Execute the core strategy logic (e.g. LLM Inference)."""
        pass
        
    @abstractmethod
    def run_pipeline(self):
        """Orchestrate the end-to-end execution of the agent."""
        pass
