# Legacy command-line predictor

This is the original project BetIQ grew from. It has two scripts that
predict Premier League and Champions League matches with a Random Forest
trained on recent form. The current app is in `backend/` and `frontend/`;
these scripts are kept for reference.

```bash
cd legacy
pip install pandas numpy scikit-learn
python safe_epl.py      # → predictions/epl_predictions.csv
python ucl_predict.py   # → predictions/cl_predictions.csv
```

They read the historical CSVs in `../data/`. `safe_epl.py` also needs the
fixture list in `epl-2025.csv`.
