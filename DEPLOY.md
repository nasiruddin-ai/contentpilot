# Deploying ContentPilot on a free server

This guide puts the whole app on one **Oracle Cloud Always Free** server. Oracle's free tier includes an ARM server with up to 4 CPUs, 24 GB of memory and 200 GB of disk, with no end date. The whole Docker setup runs on it unchanged.

You will end up with:

- `https://your-domain` serving the landing page, the app and the API.
- Free HTTPS certificates that renew automatically.
- Everything restarting by itself after a crash or reboot.
- A nightly database and image backup.

Plan about an hour the first time. Every command below is meant to be copied exactly. Replace anything in CAPITALS with your own value.

> **Before you start:** the server begins with an empty database. You will create your account, brands and sources again there, and reconnect Facebook. When the server is working, stop the copy on your PC (`docker compose down` in the `contentpilot` folder) so two copies never publish to the same Page.

---

## 1. Create the Oracle Cloud account

1. Go to https://www.oracle.com/cloud/free/ and choose **Start for free**.
2. Pick a **Home Region** close to your users, for example Singapore or Mumbai for Bangladesh. You cannot change it later.
3. Oracle asks for a card to confirm your identity. Always Free resources are not charged.

## 2. Create the server

1. In the Oracle console open **Compute → Instances → Create instance**.
2. **Name:** `contentpilot`.
3. **Image:** choose **Change image → Canonical Ubuntu 24.04**.
4. **Shape:** choose **Change shape → Ampere → VM.Standard.A1.Flex**, then set **2 OCPUs and 12 GB memory**. That is half the free allowance and plenty for this app.
5. **Networking:** keep "Create new virtual cloud network" and make sure **Assign a public IPv4 address** is on.
6. **SSH keys:** choose **Generate a key pair for me** and click **Save private key**. Keep this file safe, because it is the only way into the server. Save it as `C:\Users\YOUR-WINDOWS-NAME\.ssh\contentpilot.key`.
7. Click **Create** and wait until the state is **Running**. Copy the **Public IP address**.

If Oracle says **"Out of capacity"**, try again later or pick another availability domain. This is common with the free ARM servers.

## 3. Open the web ports in Oracle's firewall

1. On the instance page, click the **Subnet** link, then the **Default Security List**.
2. Click **Add Ingress Rules** and add:
   - Source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port range `80,443`
3. Save.

The server has a second firewall of its own. The setup script in step 6 opens that one.

## 4. Get a web address

You need a name that points at the server's IP address. Facebook login requires HTTPS, and HTTPS requires a name.

- **Your own domain:** in your domain's DNS settings add an **A record**. Use host `@` or a subdomain like `app`, with the value set to the server's public IP.
- **Free subdomain:** sign in at https://www.duckdns.org, create a subdomain such as `mycontentpilot`, set **current ip** to the server's public IP and click **update ip**. Your address is `mycontentpilot.duckdns.org`.

To check that it works, run this in PowerShell on your PC. It should print the server's IP:

```powershell
nslookup YOUR-DOMAIN
```

## 5. Connect to the server

In **PowerShell** on your PC, first make the key file private. SSH refuses keys that other Windows users can read:

```powershell
icacls "$env:USERPROFILE\.ssh\contentpilot.key" /inheritance:r /grant:r "$($env:USERNAME):R"
```

Then connect:

```powershell
ssh -i "$env:USERPROFILE\.ssh\contentpilot.key" ubuntu@SERVER-IP
```

Answer `yes` the first time. Your prompt changes to `ubuntu@contentpilot:~$`, which means you are on the server. Type `exit` to leave.

## 6. Upload the project and prepare the server

**On your PC**, in **Git Bash**, pack the project. The package leaves out your `.env` and other secrets:

```bash
cd "/c/N8N by Nasir/index.js/Squareko/contentpilot"
bash deploy/package.sh
```

**On your PC**, in **PowerShell**, upload it:

```powershell
scp -i "$env:USERPROFILE\.ssh\contentpilot.key" "C:\N8N by Nasir\index.js\Squareko\contentpilot-deploy.tar.gz" ubuntu@SERVER-IP:~
```

**On the server** (connect with `ssh` as in step 5):

```bash
mkdir -p contentpilot && tar -xzf contentpilot-deploy.tar.gz -C contentpilot
cd contentpilot
bash deploy/server-setup.sh
exit
```

The setup script installs Docker, opens ports 80 and 443 in the server's firewall and turns on automatic security updates. Reconnect afterwards so Docker works without `sudo`.

## 7. Create the settings file

**On the server:**

```bash
cd ~/contentpilot
bash deploy/init-env.sh YOUR-DOMAIN YOUR-EMAIL
nano .env
```

`init-env.sh` fills in your address and generates new random secrets for the database password, login tokens and social token encryption. In the editor, fill in:

- `GOOGLE_AI_API_KEY`: the same Gemini key you use on your PC works.
- `FACEBOOK_CLIENT_ID`, `FACEBOOK_CLIENT_SECRET`, `FACEBOOK_LOGIN_CONFIG_ID`: the same values as on your PC.
- Optional: `GEMINI_QUALITY_MODEL=gemini-3.5-flash` (remove the `#`), which is more reliable on Gemini's free tier.

Save with **Ctrl+O**, press **Enter**, then exit with **Ctrl+X**.

Never paste `.env` into chats, emails or git. Anyone with it can sign in as any user and read social tokens.

## 8. Start ContentPilot

**On the server:**

```bash
cd ~/contentpilot
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes about 5 to 10 minutes. Then check that everything is running:

```bash
docker compose -f docker-compose.prod.yml ps
```

You should see `postgres`, `redis`, `api`, `worker`, `beat`, `web` and `caddy` as **Up**. `migrate` shows **Exited (0)**, which is correct because it runs once.

Open `https://YOUR-DOMAIN` in your browser. The first visit can take up to a minute while the HTTPS certificate is issued. Then create your account.

## 9. Point Facebook at the new address

In https://developers.facebook.com → your app:

1. **App settings → Basic → App domains:** add `YOUR-DOMAIN`.
2. **Use cases → Facebook Login for Business → Settings → Valid OAuth Redirect URIs:** add
   `https://YOUR-DOMAIN/api/v1/social/facebook/callback`
3. Save.

Then in ContentPilot open **Settings → Connected accounts → Facebook → Connect**.

## 10. Turn on nightly backups

**On the server:**

```bash
crontab -e
```

Choose `nano` if asked, add this line at the bottom, then save and exit:

```
30 3 * * * cd /home/ubuntu/contentpilot && bash deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```

Every night at 03:30 (server time, UTC) the database and generated images are saved to `~/contentpilot/backups`, and files older than 14 days are deleted. Copy a backup to your PC now and then, because a backup on the same server doesn't help if the server is lost. In PowerShell:

```powershell
scp -i "$env:USERPROFILE\.ssh\contentpilot.key" "ubuntu@SERVER-IP:~/contentpilot/backups/db-*.dump" .
```

---

## Updating to a new version

Repeat the upload from step 6, then on the server:

```bash
tar -xzf ~/contentpilot-deploy.tar.gz -C ~/contentpilot
cd ~/contentpilot
bash deploy/backup.sh
docker compose -f docker-compose.prod.yml up -d --build
```

Your `.env`, database, images and certificates are kept. Database changes are applied automatically on start.

## Restoring a backup

```bash
cd ~/contentpilot
docker compose -f docker-compose.prod.yml stop api worker beat
docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U contentpilot -d contentpilot --clean --if-exists < backups/db-DATE.dump
docker compose -f docker-compose.prod.yml exec -T api tar -xzf - -C /data < backups/media-DATE.tar.gz
docker compose -f docker-compose.prod.yml start api worker beat
```

Replace `DATE` with the part of the file name you want. A backup only works together with the `.env` it was made with, because social tokens are encrypted with `TOKEN_ENCRYPTION_KEY`. Keep a private copy of `.env` somewhere safe, such as a password manager.

## Everyday commands (on the server, in `~/contentpilot`)

| What | Command |
| --- | --- |
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Recent logs of one part | `docker compose -f docker-compose.prod.yml logs --tail 100 api` (or `worker`, `beat`, `web`, `caddy`) |
| Restart everything | `docker compose -f docker-compose.prod.yml restart` |
| Stop everything | `docker compose -f docker-compose.prod.yml down` (data is kept) |
| Disk space | `df -h /` |

## Troubleshooting

| Problem | Likely cause and fix |
| --- | --- |
| Browser can't connect at all | Port 80/443 closed. Check the security list from step 3, then run `bash deploy/server-setup.sh` again. |
| Certificate error, or `caddy` logs mention "challenge failed" | The domain doesn't point at this server yet. Check `nslookup YOUR-DOMAIN`, wait a few minutes, then run `docker compose -f docker-compose.prod.yml restart caddy`. |
| "502 Bad Gateway" right after starting | The app is still starting. Wait a minute. If it continues, check `logs api` and `logs web`. |
| `api` keeps restarting and logs mention JWT_SECRET or TOKEN_ENCRYPTION_KEY | `.env` is missing a secret. Run `init-env.sh` (step 7) on a fresh `.env`. |
| Facebook says the redirect URI doesn't match | Step 9: the address must be exactly `https://YOUR-DOMAIN/api/v1/social/facebook/callback`. |
| Scheduled posts don't go out | Check `logs beat` and `logs worker`. Both must be Up. |

## What this setup does for security

- Only ports 80 and 443 are open. The database, Redis, the API and the web app are reachable only inside Docker.
- All traffic is HTTPS, with HSTS and standard security headers. Login cookies are Secure and HttpOnly.
- `.env` is readable only by your server user (`chmod 600`) and is never included in the upload package.
- Logs are size-limited so they can't fill the disk. The app never logs tokens or keys.
- On Gemini's free tier, Google may use prompts to improve its products. Switch to a paid key before real customers use the app.
