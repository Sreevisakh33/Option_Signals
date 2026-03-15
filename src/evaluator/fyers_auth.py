import os
import certifi
import urllib.parse
from dotenv import load_dotenv, set_key
from fyers_apiv3 import fyersModel

# Set certifi environment variable for Fyers SDK TLS issues
os.environ["SSL_CERT_FILE"] = certifi.where()

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
REDIRECT_URI = "https://trade.fyers.in/api-login/redirect-uri/index.html"  # Standard Fyers redirect

def generate_auth_code():
    """Step 1: Generate the login URL for the user to authenticate."""
    if not CLIENT_ID or not SECRET_KEY:
        print("❌ ERROR: FYERS_CLIENT_ID or FYERS_SECRET_KEY not found in .env file.")
        print("Please add them to your .env file and run this script again.")
        return None

    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )

    auth_link = session.generate_authcode()
    print("\n" + "="*80)
    print("🔐 FYERS AUTHENTICATION REQUIRED 🔐")
    print("="*80)
    print("1. Click the link below and log into your Fyers account.")
    print("2. After successful login, you will be redirected to a blank/error page.")
    print("3. Look at the URL of that blank page.")
    print("4. Copy the long text after 'auth_code=' and paste it here.")
    print("\n👉 LOGIN LINK:\n")
    print(auth_link)
    print("\n" + "="*80)
    
    return session

def generate_access_token(session, auth_code):
    """Step 2: Exchange the auth_code for a permanent access_token."""
    session.set_token(auth_code)
    try:
        response = session.generate_token()
        if response.get('s') == 'ok':
            access_token = response.get('access_token')
            print("\n✅ Successfully generated Access Token!")
            
            # Save token to .env file
            set_key(dotenv_path, "FYERS_ACCESS_TOKEN", access_token)
            print(f"💾 Saved FYERS_ACCESS_TOKEN to {dotenv_path}")
            return access_token
        else:
            print("\n❌ Failed to generate token. Error from Fyers:")
            print(response)
            return None
    except Exception as e:
        print(f"\n❌ Exception during token generation: {e}")
        return None

if __name__ == "__main__":
    session = generate_auth_code()
    if session:
        auth_code = input("\n📝 Paste your auth_code here: ").strip()
        
        # If the user pasted the entire URL by mistake, extract just the code
        if "auth_code=" in auth_code:
            auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(auth_code).query)['auth_code'][0]
            
        if auth_code:
            generate_access_token(session, auth_code)
        else:
            print("No auth code provided. Exiting.")
