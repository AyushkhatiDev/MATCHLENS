"""Phase 3 deterministic, evidence-backed football explanations."""
from .engine import ExplanationEngine
from .models import (ExplanationConfig, ExplanationReport, MomentExplanation, EvidenceChain,
                     SignificanceBreakdown, GameRhythmAssessment, GoalContext, ExplanationType)
from .validator import ExplanationValidator

__all__ = ["ExplanationEngine", "ExplanationConfig", "ExplanationReport", "MomentExplanation",
           "EvidenceChain", "SignificanceBreakdown", "GameRhythmAssessment", "GoalContext",
           "ExplanationType", "ExplanationValidator"]
