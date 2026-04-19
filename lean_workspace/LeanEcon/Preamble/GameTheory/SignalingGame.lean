import Mathlib

/-- Two-player signaling game with sender types, messages, receiver actions,
    and separate sender/receiver payoff functions. -/
structure signaling_game where
  TypeSpace : Type*
  MessageSpace : Type*
  ActionSpace : Type*
  prior : TypeSpace → ENNReal
  senderPayoff : TypeSpace → MessageSpace → ActionSpace → ℝ
  receiverPayoff : TypeSpace → MessageSpace → ActionSpace → ℝ
