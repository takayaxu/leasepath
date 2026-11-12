"""Causal controller state, captured immediately before FCFS dispatch.

The old nine-field contract is retained. queue_length counts all runnable
requests, including the next dispatch. No target, reference, or task ID enters
the feature vector. Ready order is additional audit evidence, not a feature.
"""
import time


def refresh_queue_features(engine, states, ready, controller, *, now=None):
    now = time.monotonic() if now is None else now
    owners = [owner for _, owner in ready if not states[owner]['done']]
    if len(owners) != len(set(owners)):
        raise ValueError('duplicate runnable owner')
    observations = {}
    for owner, (initial, observed_at) in list(engine.cache.idle.items()):
        state = states[owner]
        queued = owner in owners
        running = state['tool_running']
        if queued and running:
            raise ValueError('owner cannot be runnable and executing a tool')
        observations[owner] = {
            **initial,
            'elapsed_idle_s': initial['elapsed_idle_s'] + now - observed_at,
            'tool_name': state.get('last_tool'),
            'tool_elapsed_s': now - state['tool_started'] if running else None,
            'phase': 'queued' if queued else 'waiting_tool' if running else None,
            'is_queued': queued,
            'queue_length': len(owners),
            'retry_count': 0,  # This protocol has no implicit inference retries.
        }
        engine.observe(owner, observations[owner])
    controller.emit('scheduler_snapshot', observed_monotonic_s=now,
                    ready_owners=owners, observed_features=observations,
                    schema='causal-queue-v2')
