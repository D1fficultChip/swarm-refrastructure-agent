"""Per-thread call counters; no global monkeypatch or changes to solver policy."""
import sys
from ..reconstruction.formation import FormationReconstructor
from ..reconstruction.task import TaskReconstructor
from ..reconstruction.routes import RoutePlanner


class SolverInvocationCounter:
    def __init__(self):
        self.counts={'formation':0,'task':0,'route':0}
        self.codes={FormationReconstructor.options.__code__:'formation',TaskReconstructor.options.__code__:'task',RoutePlanner.plan.__code__:'route'}

    def start(self):
        self.previous=sys.getprofile()
        sys.setprofile(self._profile)

    def _profile(self,frame,event,arg):
        if event=='call' and frame.f_code in self.codes:self.counts[self.codes[frame.f_code]]+=1
        if self.previous:self.previous(frame,event,arg)

    def stop(self):sys.setprofile(self.previous)
