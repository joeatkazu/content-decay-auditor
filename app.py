import streamlit as st
import pandas as pd
import datetime
import os
import plotly.express as px
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# --- 1. CONFIGURATION ---
REDIRECT_URI = "http://localhost:8501" 
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']

# Required for local testing with OAuth
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Content Decay Detector", page_icon="📉", layout="wide")

# --- 2. HELPER FUNCTIONS ---

import json
import tempfile

def create_flow():
    if os.getenv('IS_PRODUCTION'):
        # 1. Load secrets from Render Env Var
        secrets_dict = json.loads(os.getenv('GSC_CLIENT_SECRETS'))
        
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp_file:
            json.dump(secrets_dict, temp_file)
            temp_path = temp_file.name
            
        # 2. Get the exact URL Google is expecting
        # We use the specific URL Google complained about in the error message
        redirect_uri = "https://content-decay-auditor.onrender.com"
            
    else:
        # Local Development
        temp_path = 'client_secrets.json'
        redirect_uri = "http://localhost:8501"

    return Flow.from_client_secrets_file(
        temp_path,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

def get_gsc_data(service, site_url, start_date, end_date):
    """Fetches search data from GSC."""
    try:
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['page'],
            'rowLimit': 5000
        }
        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        rows = response.get('rows', [])
        
        if not rows:
            return pd.DataFrame(columns=['page', 'clicks', 'impressions', 'ctr', 'position'])
        
        data = []
        for row in rows:
            data.append({
                'page': row['keys'][0],
                'clicks': row['clicks'],
                'impressions': row['impressions'],
                'ctr': row['ctr'],
                'position': row['position']
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"API Error: {e}")
        return pd.DataFrame(columns=['page', 'clicks', 'impressions', 'ctr', 'position'])

def calculate_decay(df_recent, df_past):
    """Calculates decay with a left join to ensure no columns go missing."""
    if df_recent.empty:
        return pd.DataFrame()

    # Join data on the page URL
    merged = pd.merge(df_recent, df_past, on='page', suffixes=('_now', '_then'), how='left').fillna(0)
    
    # Verify required columns exist
    required_cols = ['clicks_now', 'clicks_then', 'position_now', 'position_then']
    if not all(col in merged.columns for col in required_cols):
        return pd.DataFrame()

    # Calculations
    merged['click_diff'] = merged['clicks_now'] - merged['clicks_then']
    
    # Calculate % change safety (avoid division by zero)
    merged['pct_change'] = 0.0
    mask = merged['clicks_then'] > 0
    merged.loc[mask, 'pct_change'] = (merged['click_diff'] / merged['clicks_then']) * 100
    
    # Position difference (Positive number = Rank dropped/increased in number)
    merged['pos_diff'] = merged['position_now'] - merged['position_then']
    
    # --- THE DECAY SCORE FORMULA ---
    # We weigh Click Loss (70%) and Position Drops (30%)
    merged['decay_score'] = (abs(merged['click_diff']) * 0.7) + (merged['pos_diff'] * 0.3)
    
    return merged

# --- 3. AUTHENTICATION ---

query_params = st.query_params

if "code" in query_params and "credentials" not in st.session_state:
    try:
        flow = create_flow()
        flow.fetch_token(code=query_params["code"])
        st.session_state.credentials = flow.credentials
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Login failed: {e}")

# --- 4. MAIN USER INTERFACE ---

st.title("📉 Content Decay Detector")

if "credentials" not in st.session_state:
    st.info("👋 Welcome! Please login with your Google account.")
    flow = create_flow()
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    st.link_button("🔑 Login with Google", auth_url, type="primary")
else:
    service = build('searchconsole', 'v1', credentials=st.session_state.credentials)
    
    with st.sidebar:
        st.header("Audit Settings")
        try:
            site_list_raw = service.sites().list().execute()
            sites = [s['siteUrl'] for s in site_list_raw.get('siteEntry', []) if s['permissionLevel'] != 'siteUnverifiedUser']
            selected_site = st.selectbox("Select Property", sites)
        except:
            st.error("Failed to fetch sites.")
            sites = []

        threshold = st.slider("Min Clicks Lost to Flag", 5, 500, 25)
        
        if st.button("Logout"):
            del st.session_state.credentials
            st.rerun()

    if sites and st.button("🚀 Run Content Decay Audit", type="primary"):
        with st.spinner("Comparing current 90 days vs same period last year..."):
            # Date Logic (YoY Comparison)
            today = datetime.date.today()
            end_a = (today - datetime.timedelta(days=3)).isoformat()
            start_a = (today - datetime.timedelta(days=93)).isoformat()
            end_b = (today - datetime.timedelta(days=368)).isoformat()
            start_b = (today - datetime.timedelta(days=458)).isoformat()
            
            # Fetch
            df_recent = get_gsc_data(service, selected_site, start_a, end_a)
            df_past = get_gsc_data(service, selected_site, start_b, end_b)
            
            # Process
            decay_results = calculate_decay(df_recent, df_past)
            
            if not decay_results.empty:
                # Filter for losers only based on user threshold
                decay_final = decay_results[
                    (decay_results['click_diff'] <= -threshold) & 
                    (decay_results['pct_change'] <= -15)
                ].copy().sort_values(by='decay_score', ascending=False)
                
                if not decay_final.empty:
                    # Dashboard Metrics
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Decayed Pages", len(decay_final))
                    m2.metric("Total Clicks Lost", f"{int(decay_final['click_diff'].sum())}")
                    m3.metric("Avg Rank Drop", f"{decay_final['pos_diff'].mean():.1f} spots")
                    
                    # Visualization
                    st.subheader("Top Decay Offenders")
                    # Show top 15 most urgent pages
                    fig = px.bar(decay_final.head(15), 
                                 x='click_diff', y='page', 
                                 orientation='h', 
                                 color='decay_score', 
                                 color_continuous_scale='Reds',
                                 labels={'click_diff': 'Clicks Lost', 'page': 'URL'})
                    fig.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Detailed Data Table
                    st.subheader("Full Audit Report")
                    display_df = decay_final[['page', 'clicks_then', 'clicks_now', 'click_diff', 'pct_change', 'decay_score']].copy()
                    display_df['pct_change'] = display_df['pct_change'].map("{:.1f}%".format)
                    st.dataframe(display_df, use_container_width=True)
                
                    # CSV Export
                    csv = decay_final.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download Report as CSV", csv, "gsc_decay_report.csv", "text/csv")
                else:
                    st.success("🎉 No significant decay detected for this property with current filters!")
            else:
                st.warning("No overlapping data found. This is common if the site is new or URLs have changed.")