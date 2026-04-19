import Mathlib

/-- State transition law written on the product of current states and actions. -/
def transition_law {S A : Type*} (f : S → A → S) : S × A → S :=
  fun sa => f sa.1 sa.2
