import pandas as pd
import numpy as np
import os
import re
from sklearn.ensemble import RandomForestClassifier
from datetime import datetime

# --- CONFIGURATION ---
HISTORY_FILE = 'epl-final.csv'
NEW_FIXTURES_FILE = 'epl-2025.csv'
OUTPUT_FOLDER = "predictions/"

# --- 1. DATA LOADING & PARSING ---
# --- 1. DATA LOADING & PARSING (FIXED) ---
def load_and_process_data():
    print("Loading data...")
    
    # A. Load History
    history = pd.read_csv(HISTORY_FILE)
    history['MatchDate'] = pd.to_datetime(history['MatchDate'])
    
    # B. Load New Fixtures
    current = pd.read_csv(NEW_FIXTURES_FILE)
    
    # C. Parse Dates
    current['Date'] = pd.to_datetime(current['Date'], dayfirst=True)
    
    # D. Parse Results (Format: "4 - 2")
    def parse_score(score_str):
        if pd.isna(score_str):
            return np.nan, np.nan
        try:
            parts = str(score_str).split('-')
            return int(parts[0].strip()), int(parts[1].strip())
        except:
            return np.nan, np.nan

    # Apply parsing
    scores = current['Result'].apply(parse_score)
    current['FTHG'] = [x[0] for x in scores]
    current['FTAG'] = [x[1] for x in scores]
    
    # Determine Result (H/D/A)
    # FIX: Compare numeric scores only, default to 'Unplayed' string
    conditions = [
        (current['FTHG'] > current['FTAG']),
        (current['FTHG'] < current['FTAG']),
        (current['FTHG'] == current['FTAG'])
    ]
    choices = ['H', 'A', 'D']
    
    # Use 'Unplayed' (string) instead of np.nan (float) to avoid TypeError
    current['FullTimeResult'] = np.select(conditions, choices, default='Unplayed')
    
    # E. Map Team Names
    name_map = {
        'Man Utd': 'Man United',
        'Spurs': 'Tottenham',
        'West Ham': 'West Ham',
        'Newcastle': 'Newcastle',
        'Wolves': 'Wolves',
        "Nott'm Forest": "Nott'm Forest",
        'Leeds': 'Leeds',
        'Sunderland': 'Sunderland',
        'Burnley': 'Burnley',
        'Brighton': 'Brighton',
        'Leicester': 'Leicester',
        'Man City': 'Man City'
    }
    
    def clean_team(t):
        return name_map.get(t, t)

    current['HomeTeam'] = current['Home Team'].apply(clean_team)
    current['AwayTeam'] = current['Away Team'].apply(clean_team)
    
    # F. Split into Played and Upcoming
    # FIX: Filter based on the new 'Unplayed' string
    played = current[current['FullTimeResult'] != 'Unplayed'].copy()
    upcoming = current[current['FullTimeResult'] == 'Unplayed'].copy()
    
    return history, played, upcoming

# --- 2. MERGE & FEATURE ENGINEERING ---
def prepare_features(history, played):
    print("Merging and creating features...")
    
    # Align Columns
    # History: MatchDate, HomeTeam, AwayTeam, FullTimeResult, FullTimeHomeGoals, FullTimeAwayGoals
    h_sub = history[['MatchDate', 'HomeTeam', 'AwayTeam', 'FullTimeResult', 'FullTimeHomeGoals', 'FullTimeAwayGoals']].copy()
    h_sub.columns = ['Date', 'HomeTeam', 'AwayTeam', 'Result', 'FTHG', 'FTAG']
    
    p_sub = played[['Date', 'HomeTeam', 'AwayTeam', 'FullTimeResult', 'FTHG', 'FTAG']].copy()
    p_sub.columns = ['Date', 'HomeTeam', 'AwayTeam', 'Result', 'FTHG', 'FTAG']
    
    # Concatenate
    full_data = pd.concat([h_sub, p_sub], ignore_index=True)
    full_data.sort_values('Date', inplace=True)
    
    # --- ENGINEER FEATURES ---
    
    # 1. Targets
    full_data['TotalGoals'] = full_data['FTHG'] + full_data['FTAG']
    full_data['Target_Over15'] = (full_data['TotalGoals'] >= 2).astype(int)
    full_data['Target_Over25'] = (full_data['TotalGoals'] >= 3).astype(int)
    full_data['Target_Win'] = full_data['Result'].map({'H': 2, 'D': 1, 'A': 0})
    
    # 2. Team IDs
    all_teams = pd.concat([full_data['HomeTeam'], full_data['AwayTeam']]).unique()
    encoder = {team: i for i, team in enumerate(all_teams)}
    full_data['HomeCode'] = full_data['HomeTeam'].map(encoder)
    full_data['AwayCode'] = full_data['AwayTeam'].map(encoder)
    
    # 3. Rolling Stats (Goal Averages & Form)
    # This creates the "Memory" of the model
    
    team_stats = {team: {'goals': [], 'points': []} for team in all_teams}
    
    h_g_avg = []
    a_g_avg = []
    h_form = []
    a_form = []
    
    for idx, row in full_data.iterrows():
        ht, at = row['HomeTeam'], row['AwayTeam']
        
        # Retrieve Past Stats
        h_past_g = team_stats.get(ht, {}).get('goals', [])[-5:]
        a_past_g = team_stats.get(at, {}).get('goals', [])[-5:]
        h_past_p = team_stats.get(ht, {}).get('points', [])[-5:]
        a_past_p = team_stats.get(at, {}).get('points', [])[-5:]
        
        # Calculate Avgs (Default to 1.5 goals / 1.0 points if no history)
        h_g_avg.append(sum(h_past_g)/len(h_past_g) if h_past_g else 1.5)
        a_g_avg.append(sum(a_past_g)/len(a_past_g) if a_past_g else 1.5)
        h_form.append(sum(h_past_p)/len(h_past_p) if h_past_p else 1.0)
        a_form.append(sum(a_past_p)/len(a_past_p) if a_past_p else 1.0)
        
        # Update Stats with Current Result
        # Points
        res = row['Result']
        if res == 'H': 
            team_stats[ht]['points'].append(3)
            team_stats[at]['points'].append(0)
        elif res == 'A':
            team_stats[ht]['points'].append(0)
            team_stats[at]['points'].append(3)
        else:
            team_stats[ht]['points'].append(1)
            team_stats[at]['points'].append(1)
            
        # Goals
        team_stats[ht]['goals'].append(row['FTHG'])
        team_stats[at]['goals'].append(row['FTAG'])
        
    full_data['Home_G_Avg'] = h_g_avg
    full_data['Away_G_Avg'] = a_g_avg
    full_data['Home_Form'] = h_form
    full_data['Away_Form'] = a_form
    
    return full_data, encoder, team_stats

# --- 3. TRAINING & PREDICTION ---
if __name__ == "__main__":
    # 1. Load
    history, played, upcoming = load_and_process_data()
    
    # 2. Process
    train_df, encoder, final_stats = prepare_features(history, played)
    
    # 3. Train
    print("Training Models...")
    predictors = ['HomeCode', 'AwayCode', 'Home_G_Avg', 'Away_G_Avg', 'Home_Form', 'Away_Form']
    
    rf_win = RandomForestClassifier(n_estimators=100, min_samples_leaf=5, random_state=42)
    rf_win.fit(train_df[predictors], train_df['Target_Win'])
    
    rf_15 = RandomForestClassifier(n_estimators=100, min_samples_leaf=5, random_state=42)
    rf_15.fit(train_df[predictors], train_df['Target_Over15'])
    
    rf_25 = RandomForestClassifier(n_estimators=100, min_samples_leaf=5, random_state=42)
    rf_25.fit(train_df[predictors], train_df['Target_Over25'])
    
    # 4. Prepare Upcoming
    print("Predicting Upcoming Matches...")
    upcoming['HomeCode'] = upcoming['HomeTeam'].map(encoder)
    upcoming['AwayCode'] = upcoming['AwayTeam'].map(encoder)
    
    # Calculate Features for Upcoming (Using Final Stats from Training)
    u_h_g, u_a_g, u_h_f, u_a_f = [], [], [], []
    
    for idx, row in upcoming.iterrows():
        ht, at = row['HomeTeam'], row['AwayTeam']
        
        h_p_g = final_stats.get(ht, {}).get('goals', [])[-5:]
        a_p_g = final_stats.get(at, {}).get('goals', [])[-5:]
        h_p_p = final_stats.get(ht, {}).get('points', [])[-5:]
        a_p_p = final_stats.get(at, {}).get('points', [])[-5:]
        
        u_h_g.append(sum(h_p_g)/len(h_p_g) if h_p_g else 1.5)
        u_a_g.append(sum(a_p_g)/len(a_p_g) if a_p_g else 1.5)
        u_h_f.append(sum(h_p_p)/len(h_p_p) if h_p_p else 1.0)
        u_a_f.append(sum(a_p_p)/len(a_p_p) if a_p_p else 1.0)
        
    upcoming['Home_G_Avg'] = u_h_g
    upcoming['Away_G_Avg'] = u_a_g
    upcoming['Home_Form'] = u_h_f
    upcoming['Away_Form'] = u_a_f
    
    # Drop unknown teams
    upcoming.dropna(subset=['HomeCode', 'AwayCode'], inplace=True)
    
    # 5. Predict
    probs_win = rf_win.predict_proba(upcoming[predictors])
    probs_15 = rf_15.predict_proba(upcoming[predictors])[:, 1]
    probs_25 = rf_25.predict_proba(upcoming[predictors])[:, 1]
    
  # 6. Generate Report (NO SKIP VERSION)
    results = []
    for i, (idx, row) in enumerate(upcoming.iterrows()):
        match = f"{row['HomeTeam']} vs {row['AwayTeam']}"
        date = row['Date'].strftime('%d-%m-%Y')
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
            
        # Final Append
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
        
    # Sort by date to ensure we get the NEXT matches first
    final_df = pd.DataFrame(results)
    final_df['Date'] = pd.to_datetime(final_df['Date'], dayfirst=True)
    final_df = final_df.sort_values('Date')

    # Select only the top 20
    
    top20_df = final_df.head(10)

    print("\n--- EPL PREDICTIONS FOR THIS WEEK ---")
    print(top20_df.to_string(index=False))
    
    # Save ONLY the top 20 to CSV
    save_path = os.path.join(OUTPUT_FOLDER, 'epl_predictions.csv')
    top20_df.to_csv(save_path, index=False)
    print(f"\nPredictions saved to: {save_path}")