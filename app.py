import streamlit as st
import pandas as pd
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
import os
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go

# Page configuration
st.set_page_config(
    page_title="Content Decay Auditor",
    page_icon="📉",
    layout="wide"
)

# OAuth2 configuration
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
CLIENT_SECRETS_FILE = 'client_secrets.json'
REDIRECT_URI = 'http://localhost:8080/'

# Session state initialization
if 'credentials' not in st.session_state:
    st.session_state.credentials = None
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'sites' not in st.session_state:
    st.session_state.sites = []

def load_credentials():
    """Load credentials from session state or token file"""
    if st.session_state.credentials:
        return st.session_state.credentials
    
    # Check for token file
    if os.path.exists('token.json'):
        try:
            return Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception as e:
            st.error(f"Error loading credentials: {e}")
            return None
    return None

def save_credentials(credentials):
    """Save credentials to session state and token file"""
    st.session_state.credentials = credentials
    if credentials:
        with open('token.json', 'w') as token:
            token.write(credentials.to_json())

def authenticate():
    """Handle OAuth2 authentication"""
    if not os.path.exists(CLIENT_SECRETS_FILE):
        st.error(f"❌ Please create a `{CLIENT_SECRETS_FILE}` file with your Google OAuth2 credentials.")
        st.info("""
        To get OAuth2 credentials:
        1. Go to https://console.cloud.google.com/
        2. Create a new project or select an existing one
        3. Enable the Google Search Console API
        4. Go to Credentials → Create Credentials → OAuth 2.0 Client ID
        5. Choose "Desktop app" as the application type
        6. Download the credentials JSON file
        7. Rename it to `client_secrets.json` and place it in this directory
        """)
        return False
    
    credentials = load_credentials()
    
    if credentials and credentials.valid:
        st.session_state.authenticated = True
        return True
    
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_credentials(credentials)
            st.session_state.authenticated = True
            return True
        except Exception as e:
            st.error(f"Error refreshing credentials: {e}")
    
    # Start OAuth flow
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    
    st.session_state.oauth_state = state
    st.session_state.oauth_flow = flow
    
    st.info(f"Please visit the following URL to authenticate: [Click here]({authorization_url})")
    st.info("After authorizing, you'll be redirected. Copy the full redirect URL and paste it below:")
    
    redirect_response = st.text_input("Paste the redirect URL here:")
    
    if redirect_response:
        try:
            flow = st.session_state.oauth_flow
            flow.fetch_token(authorization_response=redirect_response)
            credentials = flow.credentials
            save_credentials(credentials)
            st.session_state.authenticated = True
            st.success("✅ Authentication successful!")
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")
    
    return False

def get_search_console_service(credentials):
    """Build and return Search Console service"""
    return build('searchconsole', 'v1', credentials=credentials)

def get_sites(service):
    """Fetch list of verified sites"""
    try:
        sites_response = service.sites().list().execute()
        sites = [site['siteUrl'] for site in sites_response.get('siteEntry', []) if site.get('permissionLevel') == 'siteOwner' or site.get('permissionLevel') == 'siteFullUser']
        return sites
    except HttpError as e:
        st.error(f"Error fetching sites: {e}")
        return []

def fetch_search_analytics(service, site_url, start_date, end_date, row_limit=25000):
    """Fetch search analytics data from Google Search Console"""
    try:
        request = {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d'),
            'dimensions': ['page'],
            'rowLimit': row_limit
        }
        
        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        
        if 'rows' not in response:
            return pd.DataFrame()
        
        rows = response.get('rows', [])
        data = []
        for row in rows:
            data.append({
                'url': row['keys'][0],
                'clicks': row.get('clicks', 0),
                'impressions': row.get('impressions', 0),
                'ctr': row.get('ctr', 0),
                'position': row.get('position', 0)
            })
        
        return pd.DataFrame(data)
    except HttpError as e:
        st.error(f"Error fetching search analytics: {e}")
        return pd.DataFrame()

def calculate_date_ranges():
    """Calculate current and previous year date ranges (90 days)"""
    today = datetime.now().date()
    
    # Current period: last 90 days
    current_end = today - timedelta(days=1)  # Yesterday (data usually has 1-2 day delay)
    current_start = current_end - timedelta(days=89)  # 90 days total
    
    # Previous period: same 90-day window one year ago
    previous_end = current_end - timedelta(days=365)
    previous_start = current_start - timedelta(days=365)
    
    return {
        'current': {'start': current_start, 'end': current_end},
        'previous': {'start': previous_start, 'end': previous_end}
    }

def main():
    st.title("📉 Content Decay Auditor")
    st.markdown("Analyze your Google Search Console data to identify URLs with significant traffic decline")
    
    # Authentication
    if not st.session_state.authenticated:
        authenticate()
        return
    
    # Get credentials and build service
    credentials = load_credentials()
    if not credentials:
        st.error("No valid credentials found. Please authenticate.")
        st.session_state.authenticated = False
        return
    
    service = get_search_console_service(credentials)
    
    # Fetch and cache sites
    if not st.session_state.sites:
        with st.spinner("Fetching verified sites..."):
            st.session_state.sites = get_sites(service)
    
    if not st.session_state.sites:
        st.warning("No verified sites found. Please verify at least one site in Google Search Console.")
        return
    
    # Site selection
    selected_site = st.selectbox("Select a verified site:", st.session_state.sites)
    
    # Calculate date ranges
    date_ranges = calculate_date_ranges()
    
    st.markdown("---")
    st.subheader("Analysis Periods")
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"**Current Period:** {date_ranges['current']['start']} to {date_ranges['current']['end']}")
    with col2:
        st.info(f"**Previous Period:** {date_ranges['previous']['start']} to {date_ranges['previous']['end']}")
    
    # Fetch data button
    if st.button("🔍 Analyze Content Decay", type="primary"):
        with st.spinner("Fetching current period data (this may take a minute)..."):
            current_df = fetch_search_analytics(
                service,
                selected_site,
                date_ranges['current']['start'],
                date_ranges['current']['end']
            )
        
        with st.spinner("Fetching previous period data (this may take a minute)..."):
            previous_df = fetch_search_analytics(
                service,
                selected_site,
                date_ranges['previous']['start'],
                date_ranges['previous']['end']
            )
        
        if current_df.empty and previous_df.empty:
            st.error("No data available for the selected periods.")
            return
        
        # Merge dataframes on URL
        merged_df = pd.merge(
            current_df,
            previous_df,
            on='url',
            how='outer',
            suffixes=('_current', '_previous')
        ).fillna(0)
        
        # Calculate changes
        merged_df['clicks_change'] = merged_df['clicks_current'] - merged_df['clicks_previous']
        merged_df['clicks_change_pct'] = (
            ((merged_df['clicks_current'] - merged_df['clicks_previous']) / 
             merged_df['clicks_previous'].replace(0, 1)) * 100
        ).round(2)
        
        merged_df['impressions_change'] = merged_df['impressions_current'] - merged_df['impressions_previous']
        merged_df['impressions_change_pct'] = (
            ((merged_df['impressions_current'] - merged_df['impressions_previous']) / 
             merged_df['impressions_previous'].replace(0, 1)) * 100
        ).round(2)
        
        # Filter URLs with >20% click decline
        decay_threshold = -20
        decay_df = merged_df[
            (merged_df['clicks_change_pct'] <= decay_threshold) & 
            (merged_df['clicks_previous'] > 0)  # Only URLs that had clicks in previous period
        ].copy()
        
        # Sort by click decline percentage
        decay_df = decay_df.sort_values('clicks_change_pct')
        
        # Store in session state for download
        st.session_state.decay_df = decay_df
        st.session_state.merged_df = merged_df
        
        # Display summary metrics
        st.markdown("---")
        st.subheader("📊 Summary Statistics")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total URLs Analyzed", len(merged_df))
        with col2:
            st.metric("URLs with >20% Click Decline", len(decay_df))
        with col3:
            if len(merged_df) > 0:
                total_current_clicks = merged_df['clicks_current'].sum()
                total_previous_clicks = merged_df['clicks_previous'].sum()
                total_change_pct = ((total_current_clicks - total_previous_clicks) / total_previous_clicks * 100) if total_previous_clicks > 0 else 0
                st.metric("Total Clicks Change", f"{total_change_pct:.1f}%", 
                         delta=f"{total_current_clicks - total_previous_clicks:,.0f}")
        with col4:
            if len(decay_df) > 0:
                decay_clicks_lost = decay_df['clicks_change'].sum()
                st.metric("Total Clicks Lost (Decay)", f"{decay_clicks_lost:,.0f}")
        
        # Display decay URLs table
        if len(decay_df) > 0:
            st.markdown("---")
            st.subheader(f"🔻 URLs with >20% Click Decline ({len(decay_df)} URLs)")
            
            # Format dataframe for display
            display_df = decay_df[[
                'url', 'clicks_current', 'clicks_previous', 'clicks_change', 'clicks_change_pct',
                'impressions_current', 'impressions_previous', 'impressions_change_pct'
            ]].copy()
            
            display_df.columns = [
                'URL', 'Current Clicks', 'Previous Clicks', 'Click Change', 'Click Change %',
                'Current Impressions', 'Previous Impressions', 'Impression Change %'
            ]
            
            # Format numbers
            display_df['Click Change %'] = display_df['Click Change %'].apply(lambda x: f"{x:.1f}%")
            display_df['Impression Change %'] = display_df['Impression Change %'].apply(lambda x: f"{x:.1f}%")
            display_df['Current Clicks'] = display_df['Current Clicks'].astype(int)
            display_df['Previous Clicks'] = display_df['Previous Clicks'].astype(int)
            display_df['Click Change'] = display_df['Click Change'].astype(int)
            
            st.dataframe(display_df, use_container_width=True, height=600)
            
            # Download button
            csv = decay_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Decay URLs as CSV",
                data=csv,
                file_name=f"content_decay_{selected_site.replace('sc-domain:', '').replace('/', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
            
            # Visualizations
            st.markdown("---")
            st.subheader("📈 Visualizations")
            
            col1, col2 = st.columns(2)
            
            with col1:
                # Top 20 URLs by click decline
                top_decay = decay_df.head(20).sort_values('clicks_change_pct')
                fig_bar = px.bar(
                    top_decay,
                    x='clicks_change_pct',
                    y='url',
                    orientation='h',
                    title='Top 20 URLs by Click Decline %',
                    labels={'clicks_change_pct': 'Click Change %', 'url': 'URL'},
                    color='clicks_change_pct',
                    color_continuous_scale='Reds_r'
                )
                fig_bar.update_layout(height=600, yaxis={'categoryorder': 'total ascending'})
                st.plotly_chart(fig_bar, use_container_width=True)
            
            with col2:
                # Click change scatter
                fig_scatter = px.scatter(
                    decay_df.head(50),
                    x='clicks_previous',
                    y='clicks_current',
                    size='clicks_change_pct',
                    hover_name='url',
                    title='Click Decline: Previous vs Current',
                    labels={'clicks_previous': 'Previous Period Clicks', 'clicks_current': 'Current Period Clicks'},
                    color='clicks_change_pct',
                    color_continuous_scale='Reds'
                )
                # Add diagonal line
                max_clicks = max(decay_df['clicks_previous'].max(), decay_df['clicks_current'].max())
                fig_scatter.add_trace(go.Scatter(
                    x=[0, max_clicks],
                    y=[0, max_clicks],
                    mode='lines',
                    line=dict(dash='dash', color='gray'),
                    name='No Change Line'
                ))
                st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            st.success("🎉 No URLs found with >20% click decline!")
            st.info("All your URLs are performing well or better than last year.")
    
    # Sidebar with logout
    with st.sidebar:
        st.header("Settings")
        if st.button("🔓 Logout"):
            st.session_state.authenticated = False
            st.session_state.credentials = None
            if os.path.exists('token.json'):
                os.remove('token.json')
            st.rerun()
        
        st.markdown("---")
        st.markdown("### About")
        st.info("""
        This tool analyzes Google Search Console data to identify content decay.
        
        **What it does:**
        - Compares the last 90 days vs the same period one year ago
        - Identifies URLs with >20% click decline
        - Shows detailed metrics and visualizations
        """)

if __name__ == "__main__":
    main()
