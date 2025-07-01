"""
Device-specific performance benchmarking for wisent-guard.

This module runs quick performance tests on the current device to measure
actual execution times for different operations, then saves those estimates
for future budget calculations.
"""

import json
import time
import os
import tempfile
import subprocess
import sys
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib


@dataclass
class DeviceBenchmark:
    """Performance benchmark results for a specific device."""
    device_id: str
    device_type: str  # "cpu", "cuda", "mps", etc.
    model_loading_seconds: float
    benchmark_eval_seconds_per_100_examples: float
    classifier_training_seconds_per_100_samples: float  # Actually measures full classifier creation time (per 100 classifiers)
    data_generation_seconds_per_example: float
    steering_seconds_per_example: float
    benchmark_timestamp: float
    python_version: str
    platform_info: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DeviceBenchmark':
        """Create from dictionary loaded from JSON."""
        return cls(**data)


class DeviceBenchmarker:
    """Runs performance benchmarks and manages device-specific estimates."""
    
    def __init__(self, benchmarks_file: str = "device_benchmarks.json"):
        self.benchmarks_file = benchmarks_file
        self.cached_benchmark: Optional[DeviceBenchmark] = None
        
    def get_device_id(self) -> str:
        """Generate a unique ID for the current device configuration."""
        import platform
        
        # Create device fingerprint from hardware/software info
        info_parts = [
            platform.machine(),
            platform.processor(),
            platform.system(),
            platform.release(),
            sys.version,
        ]
        
        # Add GPU info if available
        try:
            import torch
            if torch.cuda.is_available():
                info_parts.append(f"cuda_{torch.cuda.get_device_name()}")
            elif torch.backends.mps.is_available():
                info_parts.append("mps")
        except ImportError:
            pass
        
        # Create hash of the combined info
        combined = "|".join(str(part) for part in info_parts)
        device_hash = hashlib.md5(combined.encode()).hexdigest()[:12]
        return device_hash
    
    def get_device_type(self) -> str:
        """Detect the device type (cpu, cuda, mps, etc.)."""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            elif torch.backends.mps.is_available():
                return "mps" 
            else:
                return "cpu"
        except ImportError:
            return "cpu"
    
    def load_cached_benchmark(self) -> Optional[DeviceBenchmark]:
        """Load cached benchmark results if they exist and are recent."""
        if not os.path.exists(self.benchmarks_file):
            return None
            
        try:
            with open(self.benchmarks_file, 'r') as f:
                data = json.load(f)
            
            device_id = self.get_device_id()
            if device_id not in data:
                return None
                
            benchmark_data = data[device_id]
            benchmark = DeviceBenchmark.from_dict(benchmark_data)
            
            # Check if benchmark is recent (within 7 days)
            current_time = time.time()
            age_days = (current_time - benchmark.benchmark_timestamp) / (24 * 3600)
            
            if age_days > 7:
                print(f"   ⚠️ Cached benchmark is {age_days:.1f} days old, will re-run")
                return None
                
            return benchmark
            
        except Exception as e:
            print(f"   ⚠️ Error loading cached benchmark: {e}")
            return None
    
    def save_benchmark(self, benchmark: DeviceBenchmark) -> None:
        """Save benchmark results to JSON file."""
        try:
            # Load existing data
            existing_data = {}
            if os.path.exists(self.benchmarks_file):
                with open(self.benchmarks_file, 'r') as f:
                    existing_data = json.load(f)
            
            # Update with new benchmark
            existing_data[benchmark.device_id] = benchmark.to_dict()
            
            # Save back to file
            with open(self.benchmarks_file, 'w') as f:
                json.dump(existing_data, f, indent=2)
                
            print(f"   💾 Saved benchmark results to {self.benchmarks_file}")
            
        except Exception as e:
            print(f"   ❌ Error saving benchmark: {e}")
    
    def run_model_loading_benchmark(self) -> float:
        """Benchmark actual model loading time using the real model."""
        print("   📊 Benchmarking model loading...")
        
        # Create actual model loading test script
        test_script = '''
import time
import sys
sys.path.append('.')

start_time = time.time()
try:
    from wisent_guard.core.model import Model
    # Use the actual model that will be used in production
    model = Model("meta-llama/Llama-3.1-8B-Instruct")
    end_time = time.time()
    print(f"BENCHMARK_RESULT:{end_time - start_time}")
except Exception as e:
    print(f"BENCHMARK_ERROR:{e}")
    raise
'''
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                temp_script = f.name
            
            # Run with longer timeout for real model
            result = subprocess.run([
                sys.executable, temp_script
            ], capture_output=True, text=True, timeout=120)
            
            # Clean up
            os.unlink(temp_script)
            
            # Parse result
            for line in result.stdout.split('\n'):
                if line.startswith('BENCHMARK_RESULT:'):
                    loading_time = float(line.split(':')[1])
                    print(f"      Model loading: {loading_time:.1f}s")
                    return loading_time
                    
        except Exception as e:
            print(f"      Error in model loading benchmark: {e}")
            raise RuntimeError(f"Model loading benchmark failed: {e}")
    
    def run_benchmark_eval_test(self) -> float:
        """Benchmark evaluation performance using real CLI functionality."""
        print("   📊 Benchmarking evaluation performance...")
        
        # Create evaluation test script using actual CLI
        test_script = '''
import time
import sys
sys.path.append('.')

start_time = time.time()
try:
    from wisent_guard.cli import run_task_pipeline
    
    # Run actual evaluation with real model and minimal examples
    run_task_pipeline(
        task_name="truthfulqa_mc",
        model_name="meta-llama/Llama-3.1-8B-Instruct",
        limit=10,  # 10 examples for timing
        steering_mode=False,  # No steering for baseline timing
        verbose=False,
        allow_small_dataset=True,
        output_mode="likelihoods"
    )
    
    end_time = time.time()
    total_time = end_time - start_time
    # Scale to per-100-examples
    time_per_100 = (total_time / 10) * 100
    print(f"BENCHMARK_RESULT:{time_per_100}")
    
except Exception as e:
    print(f"BENCHMARK_ERROR:{e}")
    raise
'''
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                temp_script = f.name
            
            result = subprocess.run([
                sys.executable, temp_script
            ], capture_output=True, text=True, timeout=300)  # Longer timeout for real eval
            
            os.unlink(temp_script)
            
            # Parse result
            for line in result.stdout.split('\n'):
                if line.startswith('BENCHMARK_RESULT:'):
                    eval_time = float(line.split(':')[1])
                    print(f"      Evaluation: {eval_time:.1f}s per 100 examples")
                    return eval_time
                    
        except Exception as e:
            print(f"      Error in evaluation benchmark: {e}")
            raise RuntimeError(f"Evaluation benchmark failed: {e}")
    
    def run_classifier_training_test(self) -> float:
        """Benchmark ACTUAL classifier training using real synthetic classifier creation."""
        print("   📊 Benchmarking classifier training...")
        
        # Create test script that uses real synthetic classifier creation
        test_script = '''
import time
import sys
sys.path.append('.')

start_time = time.time()
try:
    from wisent_guard.core.model import Model
    from wisent_guard.core.agent.diagnose.synthetic_classifier_option import create_classifier_from_trait_description
    from wisent_guard.core.agent.budget import set_time_budget
    
    # Set a budget for the classifier creation
    set_time_budget(5.0)  # 5 minutes
    
    # Load the actual model
    model = Model("meta-llama/Llama-3.1-8B-Instruct")
    
    # Create ONE actual classifier using the real synthetic process
    # This includes: generating contrastive pairs + extracting activations + training
    classifier = create_classifier_from_trait_description(
        model=model,
        trait_description="accuracy and truthfulness",
        num_pairs=10  # Small number for timing
    )
    
    end_time = time.time()
    total_time = end_time - start_time
    
    # This is time for ONE complete classifier creation
    # Scale to "per 100 classifiers" for compatibility with existing code
    time_per_100 = total_time * 100
    print(f"BENCHMARK_RESULT:{time_per_100}")
    
except Exception as e:
    print(f"BENCHMARK_ERROR:{e}")
    raise
'''
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                temp_script = f.name
            
            result = subprocess.run([
                sys.executable, temp_script
            ], capture_output=True, text=True, timeout=600)  # Much longer timeout for real classifier creation
            
            os.unlink(temp_script)
            
            # Parse result
            for line in result.stdout.split('\n'):
                if line.startswith('BENCHMARK_RESULT:'):
                    training_time = float(line.split(':')[1])
                    print(f"      Classifier training: {training_time:.1f}s per 100 classifiers")
                    return training_time
                    
        except Exception as e:
            print(f"      Error in classifier training benchmark: {e}")
            raise RuntimeError(f"Classifier training benchmark failed: {e}")
    
    def run_steering_test(self) -> float:
        """Benchmark steering performance using real CLI functionality."""
        print("   📊 Benchmarking steering performance...")
        
        # Create steering test script using actual CLI
        test_script = '''
import time
import sys
sys.path.append('.')

start_time = time.time()
try:
    from wisent_guard.cli import run_task_pipeline
    
    # Run actual steering with real model and minimal examples
    run_task_pipeline(
        task_name="truthfulqa_mc",
        model_name="meta-llama/Llama-3.1-8B-Instruct",
        limit=5,  # 5 examples for timing
        steering_mode=True,
        steering_method="CAA",
        steering_strength=1.0,
        layer="15",
        verbose=False,
        allow_small_dataset=True,
        output_mode="likelihoods"
    )
    
    end_time = time.time()
    total_time = end_time - start_time
    # Time per example
    time_per_example = total_time / 5
    print(f"BENCHMARK_RESULT:{time_per_example}")
    
except Exception as e:
    print(f"BENCHMARK_ERROR:{e}")
    raise
'''
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                temp_script = f.name
            
            result = subprocess.run([
                sys.executable, temp_script
            ], capture_output=True, text=True, timeout=300)
            
            os.unlink(temp_script)
            
            # Parse result
            for line in result.stdout.split('\n'):
                if line.startswith('BENCHMARK_RESULT:'):
                    steering_time = float(line.split(':')[1])
                    print(f"      Steering: {steering_time:.1f}s per example")
                    return steering_time
                    
        except Exception as e:
            print(f"      Error in steering benchmark: {e}")
            raise RuntimeError(f"Steering benchmark failed: {e}")
    
    def run_data_generation_test(self) -> float:
        """Benchmark data generation performance using real synthetic generation.""" 
        print("   📊 Benchmarking data generation...")
        
        # Create data generation test script using actual synthetic pair generation
        test_script = '''
import time
import sys
sys.path.append('.')

start_time = time.time()
try:
    from wisent_guard.core.model import Model
    from wisent_guard.core.contrastive_pairs.generate_synthetically import SyntheticContrastivePairGenerator
    
    # Load the actual model
    model = Model("meta-llama/Llama-3.1-8B-Instruct")
    
    # Create generator and generate actual synthetic pairs
    generator = SyntheticContrastivePairGenerator(model)
    
    # Generate a small set of pairs for timing
    pair_set = generator.generate_contrastive_pair_set(
        trait_description="accuracy and truthfulness",
        num_pairs=3,  # Small number for timing
        name="benchmark_test"
    )
    
    end_time = time.time()
    total_time = end_time - start_time
    
    # Calculate time per generated pair (each pair has 2 responses)
    num_generated_responses = len(pair_set.pairs) * 2
    if num_generated_responses == 0:
        raise RuntimeError("No pairs were generated during data generation benchmark")
    
    time_per_example = total_time / num_generated_responses
    print(f"BENCHMARK_RESULT:{time_per_example}")
    
except Exception as e:
    print(f"BENCHMARK_ERROR:{e}")
    raise
'''
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                temp_script = f.name
            
            result = subprocess.run([
                sys.executable, temp_script
            ], capture_output=True, text=True, timeout=300)
            
            os.unlink(temp_script)
            
            # Parse result
            for line in result.stdout.split('\n'):
                if line.startswith('BENCHMARK_RESULT:'):
                    generation_time = float(line.split(':')[1])
                    print(f"      Data generation: {generation_time:.1f}s per example")
                    return generation_time
                    
        except Exception as e:
            print(f"      Error in data generation benchmark: {e}")
            raise RuntimeError(f"Data generation benchmark failed: {e}")
    
    def run_full_benchmark(self, force_rerun: bool = False) -> DeviceBenchmark:
        """Run complete device benchmark suite."""
        # Check for cached results first
        if not force_rerun:
            cached = self.load_cached_benchmark()
            if cached:
                print(f"   ✅ Using cached benchmark results (device: {cached.device_id[:8]}...)")
                self.cached_benchmark = cached
                return cached
        
        print("🚀 Running device performance benchmark...")
        print("   This will take 1-2 minutes to measure your hardware performance")
        
        import platform
        
        device_id = self.get_device_id()
        device_type = self.get_device_type()
        
        print(f"   🖥️ Device ID: {device_id[:8]}... ({device_type})")
        
        # Run all benchmarks
        model_loading = self.run_model_loading_benchmark()
        benchmark_eval = self.run_benchmark_eval_test()
        classifier_training = self.run_classifier_training_test()
        steering = self.run_steering_test()
        data_generation = self.run_data_generation_test()
        
        # Create benchmark result
        benchmark = DeviceBenchmark(
            device_id=device_id,
            device_type=device_type,
            model_loading_seconds=model_loading,
            benchmark_eval_seconds_per_100_examples=benchmark_eval,
            classifier_training_seconds_per_100_samples=classifier_training,
            data_generation_seconds_per_example=data_generation,
            steering_seconds_per_example=steering,
            benchmark_timestamp=time.time(),
            python_version=sys.version,
            platform_info=platform.platform()
        )
        
        # Save results
        self.save_benchmark(benchmark)
        self.cached_benchmark = benchmark
        
        print("   ✅ Benchmark complete!")
        print(f"      Model loading: {model_loading:.1f}s")
        print(f"      Evaluation: {benchmark_eval:.1f}s per 100 examples")
        print(f"      Classifier creation: {classifier_training:.1f}s per 100 classifiers")
        print(f"      Steering: {steering:.1f}s per example")
        print(f"      Generation: {data_generation:.1f}s per example")
        
        return benchmark
    
    def get_current_benchmark(self, auto_run: bool = True) -> Optional[DeviceBenchmark]:
        """Get current device benchmark, optionally auto-running if needed."""
        if self.cached_benchmark:
            return self.cached_benchmark
            
        cached = self.load_cached_benchmark()
        if cached:
            self.cached_benchmark = cached
            return cached
            
        if auto_run:
            return self.run_full_benchmark()
            
        return None
    
    def estimate_task_time(self, task_type: str, quantity: int = 1) -> float:
        """
        Estimate time for a specific task type and quantity.
        
        Args:
            task_type: Type of task ("model_loading", "benchmark_eval", etc.)
            quantity: Number of items (examples, samples, etc.)
            
        Returns:
            Estimated time in seconds
        """
        benchmark = self.get_current_benchmark()
        if not benchmark:
            raise RuntimeError(f"No benchmark available for device. Run benchmark first with: python -m wisent_guard.core.agent.budget benchmark")
        else:
            # Use actual benchmark results
            if task_type == "model_loading":
                return benchmark.model_loading_seconds
            elif task_type == "benchmark_eval":
                base_time = benchmark.benchmark_eval_seconds_per_100_examples
                return (base_time / 100.0) * quantity
            elif task_type == "classifier_training":
                base_time = benchmark.classifier_training_seconds_per_100_samples  # Actually per 100 classifiers now
                return (base_time / 100.0) * quantity
            elif task_type == "steering":
                return benchmark.steering_seconds_per_example * quantity
            elif task_type == "data_generation":
                return benchmark.data_generation_seconds_per_example * quantity
            else:
                raise ValueError(f"Unknown task type: {task_type}")


# Global benchmarker instance
_device_benchmarker = DeviceBenchmarker()


def get_device_benchmarker() -> DeviceBenchmarker:
    """Get the global device benchmarker instance."""
    return _device_benchmarker


def ensure_benchmark_exists(force_rerun: bool = False) -> DeviceBenchmark:
    """Ensure device benchmark exists, running it if necessary."""
    return _device_benchmarker.run_full_benchmark(force_rerun=force_rerun)


def estimate_task_time(task_type: str, quantity: int = 1) -> float:
    """
    Convenience function to estimate task time.
    
    Args:
        task_type: Type of task ("model_loading", "benchmark_eval", etc.)
        quantity: Number of items
        
    Returns:
        Estimated time in seconds
    """
    return _device_benchmarker.estimate_task_time(task_type, quantity)


def get_current_device_info() -> Dict[str, str]:
    """Get current device information."""
    benchmarker = get_device_benchmarker()
    return {
        "device_id": benchmarker.get_device_id(),
        "device_type": benchmarker.get_device_type()
    } 