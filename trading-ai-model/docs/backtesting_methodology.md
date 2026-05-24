# Backtesting Methodology

1. **Minimum sample size**: N ≥ 300 for any pattern family in production
2. **Random baseline**: patterns must beat `validation/random_baseline_generator.py`
3. **Walk-forward**: use `validation/walk_forward_tester.py` for out-of-sample validation
4. **Edge validation**: `validation/edge_validator.py` and `ml/evaluation/large_number_validator.py`
5. **Positive EV**: required after fees and slippage before production weight assignment
