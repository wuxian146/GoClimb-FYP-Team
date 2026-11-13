# GoClimb

**A climbing guide for Singapore and the region: find crags, check conditions, meet other climbers, and see real routes on real walls with augmented reality (AR).**

Final Year Project · Django web app + Unity/Vuforia Android app

🌐 **Live site:** https://goclimb-d2iz.onrender.com
*(Free hosting: the first visit after a quiet period can take about a minute while the server wakes up.)*

---

## Contents

- [Features](#features)
- [How the AR works](#how-the-ar-works)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Run it locally](#run-it-locally)
- [Configuration](#configuration)
- [Tests](#tests)
- [Deployment (Render + Neon)](#deployment-render--neon)
- [Admin accounts](#admin-accounts)
- [Android AR app](#android-ar-app)
- [User stories](#user-stories)
- [Team](#team)

---

## Features

### For climbers
- **Search and map** – find crags by name, rock type and grade (single grade or a range such as 6A–7A), or browse them on a map.
- **Crag pages** – description, location, hazards, routes with grades, live weather and a 3‑day forecast.
- **Wall Scan** – take a photo of a climbing wall (camera or upload); holds and possible routes are detected automatically. Scans are saved for admin review.
- **AR on real walls**
  - **Live AR in the browser** – works on computers (webcam), Android and iPhone; routes lock onto the real holds.
  - **Android AR app** (Unity + Vuforia) – opened straight from the website with one tap.
- **Personal recommendations** – set your level and preferences once; the site recommends **crags** and the **best and second‑best routes** on each wall, with the reasons.
- **Community** – posts with photos, grades and tagged places, likes and comments, friends, and booking climbs together (add to Google Calendar or download an `.ics` file).
- **Favorites, dashboard and preferences** – in the account menu.
- **Works offline (PWA)** – installable to the home screen; pages and saved walls you have opened stay available without internet.

### For admins (staff accounts only)
- **Review queue** – approve or reject scanned walls, individual routes and new places suggested by climbers.
- **Route editing** – redraw routes using only real detected holds; give every route its own name, grade, style and hold types.
- **Locations** – add crags on a map, set grade ranges and manage each crag's routes.
- **Manage** – the full admin tools for crags, routes, users and forum posts.

Admin pages are protected on the server: non‑staff accounts get *403 Forbidden* even if they type the address.

---

## How the AR works

```
                 ┌──────────────────────────────┐
  Phone/computer │  GoClimb website (Django)    │
  browser  ───── │  • Wall Scan  → hold & route │
                 │    detection (OpenCV)        │
                 │  • Live AR in the browser    │
                 │    (OpenCV.js tracking)      │
                 │  • Review, recommendations   │
                 └──────────────┬───────────────┘
                                │  /ar/ API (same login)
                 ┌──────────────┴───────────────┐
  Android app    │  GoClimbAR (Unity + Vuforia) │
                 │  • live AR camera            │
                 │  • offline scanning          │
                 └──────────────────────────────┘
```

| | Website (all devices) | Android app |
|---|---|---|
| Scan a new wall and upload it | ✅ from a photo | ✅ live camera |
| Works offline | Saved walls only | ✅ also scans new walls offline |
| AR route overlay | ✅ browser Live AR | ✅ Vuforia |

Wall photos and route data are backed up in the database. If the server's disk is wiped (normal on free hosting), the route engine rebuilds every wall automatically on the first request, keeping the same wall, hold and route IDs.

---

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python 3.12, Django 5.2, Django REST Framework |
| Database | SQLite (local) · PostgreSQL on Neon (production) |
| Hold detection and routes | OpenCV, NumPy (`goclimb_routes/`) |
| Browser AR | OpenCV.js (ORB features + homography) |
| Frontend | Django templates, plain JavaScript, Leaflet maps |
| Offline | Service worker + web app manifest (PWA) |
| Hosting | Render (web), Neon (database), WhiteNoise (static files) |
| Android AR | Unity 2022.3 + Vuforia Engine (separate repository) |

---

## Project structure

```
GoClimb-FYP-Team/
├── config/            Django settings, URLs, request timing, password hashing
├── climbing/          Crags, routes, search, map, favorites, dashboard, forum API, Manage pages
├── wallscan/          Wall Scan, review queue, locations, AR pages, browser AR, wall restore
├── goclimb_routes/    Route engine: hold detection, route planning, wall storage
├── preferences/       Climber level and preferences, crag and route recommendations
├── community/         Posts with photos/places, friends, booked climbs, calendar export
├── pwa/               Service worker, manifest and offline page
├── templates/         Shared page templates (base layout, crag pages, auth, marketing)
├── build.sh           Build steps used by Render
├── render.yaml        Render service definition
└── requirements.txt   Python packages
```

---

## Run it locally

**Requirements:** Python 3.12 and Git.

```bash
git clone https://github.com/chenjingen-jane/GoClimb-FYP-Team.git
cd GoClimb-FYP-Team

python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env

python manage.py migrate
python manage.py seed           # optional: demo crags and routes
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000.

### Testing on a phone

Phones only allow the camera, Live AR and offline mode on `https` or `localhost`.

- **USB (Android, recommended):** `adb reverse tcp:8000 tcp:8000`, then open `http://localhost:8000` on the phone.
- **Same Wi‑Fi:** start with `ALLOWED_HOSTS=*` and `python manage.py runserver 0.0.0.0:8000`, then open `http://<your-PC-IP>:8000`. Photo upload works this way; the in‑page camera needs `https`.
- **iPhone:** use the deployed `https` site.

---

## Configuration

Settings come from environment variables (or a local `.env` file). **Never commit real values.**

| Variable | Needed | Purpose |
|---|---|---|
| `SECRET_KEY` | Production | Django secret key |
| `DEBUG` | – | `True` locally, `False` in production |
| `DATABASE_URL` | Production | PostgreSQL connection string (Neon: direct connection, `sslmode=require`) |
| `ALLOWED_HOSTS` | Production | e.g. `.onrender.com` |
| `CSRF_TRUSTED_ORIGINS` | Production | e.g. `https://*.onrender.com` |
| `GOCLIMB_ADMIN_TOKEN` | Optional | Token for the old standalone wall editor |
| `GOCLIMB_AR_APP_URL` | Optional | Download link for the Android APK (shows a download button) |
| `GOCLIMB_AR_ANDROID_PACKAGE` | Optional | Android package name of the AR app |
| `GOCLIMB_AR_DATA` | Optional | Folder for the route engine's working files (default `goclimb_data/`) |

---

## Tests

```bash
python manage.py test
```

Runs every test: crags and search, the route engine, Wall Scan, multi‑route recommendations, admin tools, community photos and wall restore.

---

## Deployment (Render + Neon)

The site runs on Render's free web service with a free Neon PostgreSQL database, both in Singapore.

1. **Neon:** create a project in *AWS Asia Pacific (Singapore)* and copy the **direct** connection string (pooling off).
2. **Render:** *New → Blueprint* → this repository. Render reads `render.yaml`.
3. Fill in `DATABASE_URL`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. `SECRET_KEY` and `GOCLIMB_ADMIN_TOKEN` are generated.
4. Every `git push` to the deployed branch redeploys automatically.

**Free‑tier behaviour to know about**
- The web service sleeps after 15 minutes without visitors; the next visit takes about a minute. Open the site a few minutes before a demo.
- The disk is wiped on every restart; walls are rebuilt from the database automatically. To trigger it by hand: `python manage.py restore_walls`.
- Logins take a few seconds because password checking is deliberately slow (security) and the free server has 0.1 CPU.
- Slow requests are written to the Render logs as `[timing] SLOW …` lines, with database time and query count.

**Moving data from local SQLite to Neon**
```bash
python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions -e admin.logentry --indent 2 -o data_export.json
# set DATABASE_URL to the Neon string, then:
python manage.py migrate
python manage.py loaddata data_export.json
```
Delete `data_export.json` afterwards: it contains password hashes.

---

## Admin accounts

There is intentionally no public "register as admin". To make an account an admin:
- **On the site:** an existing superuser opens `/admin/` → *Users* → ticks **Staff status** (and **Superuser status** for full access).
- **From the command line:** `python manage.py createsuperuser`. For the live site, run it on your computer with `DATABASE_URL` pointing at the production database.

Admins see an **Admin** menu (Review, Locations, Manage) with a count of items waiting for review.

---

## Android AR app

The Unity + Vuforia app lives in a separate repository: **GoClimbAR**.

- Build with Unity 2022.3 (Android Build Support module) and Vuforia Engine.
- The website opens it with `goclimb://` links and passes the login, so there is no second sign‑in.
- Set the app's server to `https://<your-site>/ar`. Opening a wall from the website sets it automatically.
- To offer the APK on the website, upload it somewhere public and set `GOCLIMB_AR_APP_URL`.

iPhone users use the browser Live AR instead (an APK can't be installed on iOS).

---

## User stories

<details>
<summary>All 37 user stories from the project backlog</summary>

- **#1** As an unregistered user, i want create a account so that i can have access to the system.
- **#2** As a climber, I want to log into my account, so that I can re-access my account back.
- **#3** As a climber, I want to log out of my account, so that I can safely exit the system and protect my personal information.
- **#4** As a climber, I want to create an account, so that I can have a personalized account on this platform.
- **#5** As a climber, I want to stay logged in, so that I do not have to enter my credentials every time I open the app.
- **#6** As a climber, I want to reset my password, so that I can regain access to my account if I forget it.
- **#7** As an admin, I want to log into a secure Django administrative dashboard so that I can manage platform settings and system records.
- **#8** As an admin, I want to add, edit, or delete crag profiles and route details so that the application data remains accurate and up to date.
- **#9** As an admin, I want to upload custom target images and 3D route coordinates so that the AR engine can accurately recognize new rock faces.
- **#10** As an admin, I want to view and manage registered user accounts so that I can suspend or deactivate malicious users.
- **#11** As an admin, I want to delete inappropriate or offensive messages from the community forum so that the platform maintains a safe and respectful environment.
- **#12** As a climber, I want to search a crag name so that I can immediately find its dedicated profile page.
- **#13** As a climber, I want to see all available demo crags marked as pins on an interactive map so that I can easily locate nearby climbing spots.
- **#14** As a climber, I want to click on a crag pin or card so that I can view a detailed profile screen showing its description, GPS location, and rock type.
- **#15** As a climber, I want to view a full list of climbing routes available at my selected crag so that I can plan my climbing activity in advance.
- **#16** As a climber, I want to save my favorite crag, so that I can access it again later.
- **#17** As a climber, I want to view all my saved favorite crags so that I can quickly access the climbing locations I am interested in.
- **#18** As a climber, I want to remove a crag from my Favorites list so that I can keep my saved locations organized.
- **#19** As a climber, I want to filter crags by rock type or difficulty so that I can quickly find climbing locations that match my preferences.
- **#20** As a climber, I want to view a detailed information such as its grade, length, and description, so that I can plan my climbing session according to my own needs.
- **#21** As a climber, I want to see the current temperature and weather icon displayed at the top of the crag page so that I know the real-time conditions before heading out.
- **#22** As a climber, I want to view a 3-day weather forecast including temperature, rainfall, and wind speed so that I can plan my climbing trip safely.
- **#23** As a climber, I want to get a hazard information, so that I can plan and perform my rock climbing activity safely.
- **#24** As a climber, I want to activate the AR camera to scan the rock wall, so that I can identify climbing targets.
- **#25** As a climber, I want to view an AR route overlay through my camera, so that I can easily visualize the climbing path on the wall.
- **#26** As a climber, I want to select an AR route on my screen, so that I can view its difficulty grade and hold details.
- **#27** As a climber, I want to interact with a 3D model of the rock wall, so that I can explore routes when AR is unavailable.
- **#28** As a climber, I want to share my personal climbing routes, so that beginners can follow my path.
- **#29** As a climber, I want to join community, so that I can connect with other local climbers.
- **#30** As a climber, I want to have a functioning messaging features, so that I can have online conversations with another climbers.
- **#31** As a climber, I want to view local climbers in my area, so that I can plan a rock climbing trip together.
- **#32** As a climber, I want to add other users to my friends list, so that I can build connections with them.
- **#33** As a climber, I want to access my personal dashboard so that I can view my climbing history, favorite crags, achievements, and profile information.
- **#34** As a climber, I want my completed routes to be automatically tracked, so that I can review my climbing history.
- **#35** As a climber, I want to update my profile information so that my account details remain accurate and up to date.
- **#36** As a climber, I want to report inappropriate messages from the community forum, so that moderators can review them.
- **#37** As a climber, I want to select a date and time to view the expected crowd level, so that I can plan my climbing session accordingly.

</details>

---

## Team

| Name | GitHub | Role |
| Jingen | [@chenjingen-jane](https://github.com/chenjingen-jane) | *Primarily responsible for AR integration, development and deployment, as well as preliminary frontend design* |
| Wuxian | [@wuxian146](https://github.com/wuxian146) | *Responsilble for deployment tasks(excluding AR-related components), including frontend and backend API integration* |
| Marvin | [@Mrvnp478](https://github.com/Mrvnp478) | *Responsilble for Marketing video, Landing Page, and report generating* |
| Harrods | [@HVG2K](https://github.com/HVG2K) | *Responsilble for report generating* |

*Final Year Project. Climbing is dangerous: grades and routes shown in the app are guidance only. Always check conditions and climb within your ability.*
