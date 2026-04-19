/--
A witness-based Nash existence certificate records an explicit strategy profile
that already satisfies the equilibrium predicate of the game at hand.
-/
structure HasNashEquilibrium (Profile : Type) where
  isNash : Profile → Prop
  witness : Profile
  is_nash : isNash witness

/--
Once a Nash witness has been constructed, equilibrium existence is immediate.
This is the minimal interface needed for the first v3 rebuild.
-/
theorem nash_exists_of_witness {Profile : Type}
    (h : HasNashEquilibrium Profile) : ∃ profile, h.isNash profile := by
  exact ⟨h.witness, h.is_nash⟩
