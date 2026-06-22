import pandas as pd
import numpy as np
import glob
import os
import re
from sklearn.ensemble import RandomForestClassifier
from datetime import datetime

# --- CONFIGURATION ---
# We load ALL csv files that start with "champions-league-"
FILE_PATTERN = "champions-league-*.csv"
OUTPUT_FOLDER = "predictions/"

# --- 1. DATA LOADING & CLEANING ---
def load_and_clean_data():
    print("Loading Champions League data...")
    
    files = glob.glob(FILE_PATTERN)
    files.sort() # Ensure we load 2017 -> 2025 in order
    
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f)
            # Normalize column names (some files might differ slightly)
            df.columns = [c.strip() for c in df.columns]
            dfs.append(df)
        except Exception as e:
            print(f"Skipping {f}: {e}")
            
    if not dfs:
        raise ValueError("No files found!")
        
    full_data = pd.concat(dfs, ignore_index=True)
    
    # --- A. PARSE DATES ---
    # CL files usually use DD/MM/YYYY HH:MM
    full_data['Date'] = pd.to_datetime(full_data['Date'], dayfirst=True, errors='coerce')
    full_data.sort_values('Date', inplace=True)
    
    # --- B. PARSE RESULTS (Format: "1 - 3") ---
    def parse_score(s):
        if pd.isna(s): return np.nan, np.nan
        try:
            # Remove any non-score chars if present
            s = str(s).strip()
            parts = s.split('-')
            return int(parts[0]), int(parts[1])
        except:
            return np.nan, np.nan

    scores = full_data['Result'].apply(parse_score)
    full_data['FTHG'] = [x[0] for x in scores]
    full_data['FTAG'] = [x[1] for x in scores]
    
    # Determine Outcome
    conditions = [
        (full_data['FTHG'] > full_data['FTAG']),
        (full_data['FTHG'] < full_data['FTAG']),
        (full_data['FTHG'] == full_data['FTAG'])
    ]
    choices = ['H', 'A', 'D']
    full_data['Outcome'] = np.select(conditions, choices, default='Unplayed')
    
    # --- C. NORMALIZE TEAM NAMES (CRITICAL FOR CL) ---
    # Map variations to a single standard name
    name_map = {
        'Man. City': 'Man City', 'Manchester City': 'Man City',
        'Man. United': 'Man United', 'Manchester United': 'Man United',
        'Bayern München': 'Bayern Munich', 'Bayern': 'Bayern Munich',
        'B. Dortmund': 'Dortmund', 'Borussia Dortmund': 'Dortmund',
        'Atlético': 'Atlético Madrid', 'Atleti': 'Atlético Madrid', 'Atlético de Madrid': 'Atlético Madrid',
        'Paris Saint-Germain': 'Paris',
        'Internazionale': 'Inter',
        'Tottenham Hotspur': 'Tottenham',
        'RB Leipzig': 'Leipzig',
        'FC Porto': 'Porto',
        'CSKA Moskva': 'CSKA Moscow',
        'Shakhtar Donetsk': 'Shakhtar',
        'LOSC': 'Lille',
        'Crvena zvezda': 'Crvena Zvezda',
        'GNK Dinamo': 'Dinamo Zagreb'
    }
    
    def clean_name(t):
        t = str(t).strip()
        return name_map.get(t, t)

    full_data['HomeTeam'] = full_data['Home Team'].apply(clean_name)
    full_data['AwayTeam'] = full_data['Away Team'].apply(clean_name)
    
    # Split
    played = full_data[full_data['Outcome'] != 'Unplayed'].copy()
    upcoming = full_data[full_data['Outcome'] == 'Unplayed'].copy()
    
    return played, upcoming

# --- 2. FEATURE ENGINEERING ---
def engineer_features(played, upcoming):
    print("Engineering features (Rolling Stats)...")
    
    # Combine strictly for calculation (Sorted by Date)
    # We need to calculate stats for upcoming games based on played ones
    all_rows = pd.concat([played, upcoming], ignore_index=True)
    all_rows.sort_values('Date', inplace=True)
    
    # Targets (Only valid for played rows)
    all_rows['Target_Win'] = all_rows['Outcome'].map({'H': 2, 'D': 1, 'A': 0})
    all_rows['TotalGoals'] = all_rows['FTHG'] + all_rows['FTAG']
    all_rows['Target_Over15'] = (all_rows['TotalGoals'] >= 2).astype(float)
    all_rows['Target_Over25'] = (all_rows['TotalGoals'] >= 3).astype(float)
    
    # Encoder
    teams = pd.concat([all_rows['HomeTeam'], all_rows['AwayTeam']]).unique()
    encoder = {t: i for i, t in enumerate(teams)}
    all_rows['HomeCode'] = all_rows['HomeTeam'].map(encoder)
    all_rows['AwayCode'] = all_rows['AwayTeam'].map(encoder)
    
    # --- ROLLING STATS ---
    # CL is tricky because games are far apart. 
    # We will use a "Last 5 CL Games" form.
    
    team_stats = {t: {'pts': [], 'goals': []} for t in teams}
    
    cols_to_fill = ['Home_Form', 'Away_Form', 'Home_G_Avg', 'Away_G_Avg']
    for c in cols_to_fill: all_rows[c] = 0.0
    
    # Iterate to fill features without data leakage
    for idx, row in all_rows.iterrows():
        ht, at = row['HomeTeam'], row['AwayTeam']
        
        # 1. GET STATS (Before this match)
        h_pts = team_stats[ht]['pts'][-5:]
        a_pts = team_stats[at]['pts'][-5:]
        h_gls = team_stats[ht]['goals'][-5:]
        a_gls = team_stats[at]['goals'][-5:]
        
        # Defaults if no history
        all_rows.at[idx, 'Home_Form'] = sum(h_pts)/len(h_pts) if h_pts else 1.3
        all_rows.at[idx, 'Away_Form'] = sum(a_pts)/len(a_pts) if a_pts else 1.3
        all_rows.at[idx, 'Home_G_Avg'] = sum(h_gls)/len(h_gls) if h_gls else 1.5
        all_rows.at[idx, 'Away_G_Avg'] = sum(a_gls)/len(a_gls) if a_gls else 1.5
        
        # 2. UPDATE STATS (After this match)
        if row['Outcome'] != 'Unplayed':
            # Points
            if row['Outcome'] == 'H':
                team_stats[ht]['pts'].append(3); team_stats[at]['pts'].append(0)
            elif row['Outcome'] == 'A':
                team_stats[ht]['pts'].append(0); team_stats[at]['pts'].append(3)
            else:
                team_stats[ht]['pts'].append(1); team_stats[at]['pts'].append(1)
            
            # Goals
            team_stats[ht]['goals'].append(row['FTHG'])
            team_stats[at]['goals'].append(row['FTAG'])
            
    # Re-Split
    train = all_rows[all_rows['Outcome'] != 'Unplayed'].copy()
    predict = all_rows[all_rows['Outcome'] == 'Unplayed'].copy()
    
    return train, predict

# --- 3. TRAINING & PREDICTION ---
def run_predictions():
    # Load
    played, upcoming = load_and_clean_data()
    
    if upcoming.empty:
        print("No upcoming matches found (Check if 2025 file has empty results).")
        return

    # Features
    train, predict = engineer_features(played, upcoming)
    
    # Train
    print("Training Random Forest...")
    predictors = ['HomeCode', 'AwayCode', 'Home_Form', 'Away_Form', 'Home_G_Avg', 'Away_G_Avg']
    
    rf_win = RandomForestClassifier(n_estimators=100, min_samples_leaf=3, random_state=42)
    rf_win.fit(train[predictors], train['Target_Win'])
    
    rf_15 = RandomForestClassifier(n_estimators=100, min_samples_leaf=3, random_state=42)
    rf_15.fit(train[predictors], train['Target_Over15'])
    
    rf_25 = RandomForestClassifier(n_estimators=100, min_samples_leaf=3, random_state=42)
    rf_25.fit(train[predictors], train['Target_Over25'])
    
    # Predict
    print("Generating Predictions...")
    probs_win = rf_win.predict_proba(predict[predictors])
    probs_15 = rf_15.predict_proba(predict[predictors])[:, 1]
    probs_25 = rf_25.predict_proba(predict[predictors])[:, 1]
    
    results = []
    for i, (idx, row) in enumerate(predict.iterrows()):
        date = row['Date'].strftime('%Y-%m-%d')
        match = f"{row['HomeTeam']} vs {row['AwayTeam']}"
       # Probabilities
        p_h = probs_win[i][2]
        p_d = probs_win[i][1]
        p_a = probs_win[i][0]

        # --- GOAL PROBABILITIES ---
        p_o15 = probs_15[i]   # Prob Over 1.5
        p_o25 = probs_25[i]   # Prob Over 2.5
        p_u25 = 1.0 - p_o25   # Prob Under 2.5 (Inverse of Over)
        
        # Logic
        if p_h > 0.55: 
            tip_1x2 = "Home Win (1)"
        elif p_a > 0.55: 
            tip_1x2 = "Away Win (2)"
        # Logic: If Draw probability is unusually high (> 33%) and teams are balanced
        elif p_d > 0.33: 
            tip_1x2 = "Draw (X)"
        elif (p_h + p_d) > 0.75: 
            tip_1x2 = "Home or Draw (1X)"
        elif (p_a + p_d) > 0.75: 
            tip_1x2 = "Away or Draw (2X)"
        elif (p_h + p_a) > 0.75: 
            tip_1x2 = "Home or Away (12)"
        else:
            tip_1x2 = "Skip"
        
        tip_goals = "Skip"
        # GOAL LOGIC (SAFETY FIRST MODE)
        tip_goals = "Skip"
        
        # 1. The "Banker" (Extremely Safe)
        # If the model thinks 3 goals are likely, betting on 2 is very safe.
        if p_o15 > 0.80: 
            tip_goals = "Over 1.5 Goals (Banker)"
            
        # 2. The "Refundable" 2.0
        # If Over 2.5 is decent (>55%), play Over 2.0 Asian.
        # Refund if 2 goals. Win if 3.
        elif p_o25 > 0.55: 
            tip_goals = "Over 2.0 (Asian)" 
            
        # 3. The "Refundable" 1.0
        # If Over 1.5 is decent (>65%), play Over 1.0 Asian.
        # Refund if 1 goal. Win if 2. (Very hard to lose this).
        elif p_o15 > 0.65: 
            tip_goals = "Over 1.0 (Asian)"
            
        # 4. Safe Unders
        # If Under 2.5 is likely, play Under 3.0 Asian.
        # Refund if 3 goals. Win if 0, 1, 2.
        elif p_u25 > 0.60:
            tip_goals = "Under 3.0 (Asian)"
            
        # 5. Last Resort (Avoid 0-0)
        # If we have absolutely no idea, but the home team is strong
        elif p_h > 0.60:
            tip_goals = "Over 1.0 (Asian)"
        
        results.append({
            'Date': date,
            'Match': match,
            '1X2 Prediction': tip_1x2,
            'Goal Prediction': tip_goals,
            'Home Win Confidence': f"{(p_h):.0%}",
            'Draw Confidence': f"{p_d:.0%}",
            'Away Win Confidence': f"{(p_a):.0%}",
            'Goal Confidence': f"{max(p_o25, p_u25):.0%}" # Shows confidence in the picked outcome (Over OR Under)
        })
    
    # Output
    final_df = pd.DataFrame(results).sort_values('Date')
    print("\n--- CHAMPIONS LEAGUE PREDICTIONS FOR THE NEXT MATCHDAY ---")
    top20_df = final_df.head(18)
    print(top20_df.to_string(index=False))
    save_path = os.path.join(OUTPUT_FOLDER, 'cl_predictions.csv')
    top20_df.to_csv(save_path, index=False)
    print(f"\nPredictions saved to: {save_path}")

if __name__ == "__main__":
    run_predictions()