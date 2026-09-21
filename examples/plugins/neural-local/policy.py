"""Lazy optional dependency; launch with tools/play_neural.py for prewarming."""
import os


def create_policy(settings):
    from uno.neural.policy import NeuralPolicy
    checkpoint = os.environ.get('UNO_NEURAL_CHECKPOINT')
    if not checkpoint:
        raise RuntimeError('Launch with launch_neural.cmd or set UNO_NEURAL_CHECKPOINT')
    return NeuralPolicy(checkpoint, deterministic=settings['deterministic'])
