AI Football Predictor:
A Python tool that predicts English Premier League (EPL) and Champions League (UCL) match outcomes using Machine Learning and historical data.

Requirements:
You need Python installed. Run this command to install the necessary libraries:
    pip install pandas numpy scikit-learn

How to Run
For Premier League Predictions: Run the command: python safe-epl.py Results will be saved to: predictions/EPL_predictions.csv

For Champions League Predictions: Run the command: python ucl_predict.py Results will be saved to: predictions/cl_predictions.csv

How it Works
The script analyzes the last 5 games for every team to calculate form, attack strength, and defensive strength. It uses a Random Forest Classifier to compare these stats against 7 years of history to generate probabilities.

It prioritizes "Safe Bets":

Bankers: High probability outcomes (e.g., Over 1.5 Goals).

Asian Lines: Refundable bets (e.g., Over 2.0) to minimize risk.

Double Chance: 1X or 2X when a straight win is uncertain.

Disclaimer
This tool is for educational purposes only. Probabilities are based on historical statistics and do not guarantee future results. Gamble responsibly.