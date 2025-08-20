import msal
import requests

# Config
TENANT_ID = "<tenant-id>"
CLIENT_ID = "<app-id>"
CLIENT_SECRET = "<secret>"  # or cert
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["https://graph.microsoft.com/.default"]

# 1. Acquire token
app = msal.ConfidentialClientApplication(
    CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET
)
token_result = app.acquire_token_for_client(scopes=SCOPE)

if "access_token" not in token_result:
    raise Exception("Auth failed:", token_result.get("error_description"))

access_token = token_result["access_token"]

# 2. Download a file (replace site-id & item-id)
site_id = "<site-id>"
item_id = "<drive-item-id>"  # e.g. from /drives or /children calls
url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/content"

resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
if resp.status_code == 200:
    with open("downloaded.docx", "wb") as f:
        f.write(resp.content)
    print("File saved.")
else:
    print("Error:", resp.status_code, resp.text)
