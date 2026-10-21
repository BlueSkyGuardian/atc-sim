"""Conflict geometry and resolution.

traj     piecewise-linear 4D trajectories (the system's prediction: route legs, climbs and descents), with clearance
         candidates applied (speed, heading or delaying vector then resume, level, direct).
cpa      closest points of approach: the instantaneous straight-line matrices T*, D* (and the vertical gap at t*),
         route-aware pair evaluation leg by leg with vertical windows, and the conflict matrix of a whole situation.
vo       velocity obstacles: for an aircraft, the headings and speeds that bring it within the separation minimum of
         each other aircraft (the union over all of them is what it must avoid).
resolve  the solution set of a situation: every single clearance (then pairs) that leaves no conflict with ANY
         aircraft, costed in the controller's order (speed < heading < level, smaller < larger), aircraft without
         priority first; checked leg by leg, the best confirmed by running the simulator ahead.
hj       Hamilton-Jacobi reachability tables for a pair at close range (turning dynamics), looked up per pair and
         intersected over all aircraft.
"""
