import Mathlib.Data.Real.Basic

/--
A Walrasian equilibrium packages prices with an allocation that is feasible,
optimal at those prices, and clears every market through the supplied excess
demand representation.
-/
structure WalrasianEquilibrium {Agent Commodity : Type*}
    (isFeasible : (Agent → Commodity → ℝ) → Prop)
    (isOptimal : (Commodity → ℝ) → (Agent → Commodity → ℝ) → Prop)
    (excessDemand : (Agent → Commodity → ℝ) → Commodity → ℝ) where
  price : Commodity → ℝ
  allocation : Agent → Commodity → ℝ
  feasible : isFeasible allocation
  optimal : isOptimal price allocation
  clears : ∀ good, excessDemand allocation good = 0

/--
Market clearing can be read off directly from a Walrasian equilibrium witness.
-/
theorem WalrasianEquilibrium.marketClearing {Agent Commodity : Type*}
    {isFeasible : (Agent → Commodity → ℝ) → Prop}
    {isOptimal : (Commodity → ℝ) → (Agent → Commodity → ℝ) → Prop}
    {excessDemand : (Agent → Commodity → ℝ) → Commodity → ℝ}
    (h : WalrasianEquilibrium isFeasible isOptimal excessDemand) (good : Commodity) :
    excessDemand h.allocation good = 0 := by
  exact h.clears good
