import Mathlib.Data.Real.Basic

/--
A direct mechanism is truthful when truthful reporting weakly dominates every
misreport for each agent type.
-/
def IsTruthfulDirectMechanism {AgentType Outcome : Type*}
    (utility : AgentType → Outcome → ℝ) (allocation : AgentType → Outcome) : Prop :=
  ∀ trueType misreport,
    utility trueType (allocation misreport) ≤ utility trueType (allocation trueType)

/--
Truth-telling is weakly optimal by definition in a truthful direct mechanism.
-/
theorem IsTruthfulDirectMechanism.truthful_is_best {AgentType Outcome : Type*}
    {utility : AgentType → Outcome → ℝ} {allocation : AgentType → Outcome}
    (h : IsTruthfulDirectMechanism utility allocation)
    (trueType misreport : AgentType) :
    utility trueType (allocation misreport) ≤ utility trueType (allocation trueType) := by
  exact h trueType misreport
