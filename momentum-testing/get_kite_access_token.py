from kiteconnect import KiteConnect

API_KEY = "12ycz7bv8czujypi"          # your key
API_SECRET = "r9tb7o78r7beu06u72lyn4za5mplj3pf"  # your secret

kite = KiteConnect(api_key=API_KEY)

# 1) Open this URL in your browser, login, approve the app.
print("Login URL:", kite.login_url())

# 2) After login, you'll be redirected to your Redirect URL with ?request_token=XXXX
request_token = input("Paste request_token from the URL: ").strip()

# 3) Exchange for an access token (valid for the day)
session = kite.generate_session(request_token, api_secret=API_SECRET)
print("ACCESS_TOKEN:", session["access_token"])
