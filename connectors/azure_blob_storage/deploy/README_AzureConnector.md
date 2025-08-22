Where to get/set it

Azure portal

Go to your Storage account → Security + networking → Access keys.

Click Show keys and copy the Connection string for key1 or key2. Rotate keys here when needed.
Microsoft Learn
+1

Azure CLI

az storage account show-connection-string \
-g <resource-group> -n <storage-account-name> -o tsv


This prints the same DefaultEndpointsProtocol=...;AccountName=...;AccountKey=... string.
