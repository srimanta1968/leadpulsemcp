# System Under Test (SUT) Setup Guide

> **TL;DR**: Your web app under test **must** be running on the **host machine** and **must bind to `0.0.0.0`** (all interfaces), not `127.0.0.1` (loopback only). The Test MCP and Dev MCP run inside Docker containers and reach your app via `host.docker.internal` — which only works when the app accepts connections on all interfaces.

This guide explains **why** this matters, **how to start** every common backend and frontend framework on `0.0.0.0`, and **how to verify** that your SUT is reachable from inside the MCP containers.

## Table of contents

- [Why this matters](#why-this-matters)
- [The error you will see if you get this wrong](#the-error-you-will-see-if-you-get-this-wrong)
- [Backend frameworks](#backend-frameworks)
- [Frontend frameworks](#frontend-frameworks)
- [How to verify your SUT is bound correctly](#how-to-verify-your-sut-is-bound-correctly)
- [How to verify the MCP container can reach your SUT](#how-to-verify-the-mcp-container-can-reach-your-sut)
- [Windows Firewall](#windows-firewall)
- [Troubleshooting flowchart](#troubleshooting-flowchart)

---

## Why this matters

The **Dev MCP** and **Test MCP** both run inside Docker containers. Your web application (the **SUT** — System Under Test) runs **natively on your host machine**. When the MCP container executes a test that calls your app, the traffic flows like this:

```
[Test MCP container]  ───► host.docker.internal:PORT  ───► [Your SUT on host]
 (inside Docker VM)         (resolves to 192.168.65.254           (must be listening
                             on Docker Desktop)                    on 0.0.0.0:PORT)
```

Docker Desktop routes `host.docker.internal` traffic to the host through a **virtual network adapter**, not through loopback (`127.0.0.1`). That means:

| Host bind address | Container → host traffic | Result |
|---|---|---|
| `0.0.0.0:PORT` (all interfaces) | ✅ Accepted on vEthernet adapter | **Works** |
| `[::]:PORT` (IPv6 all interfaces) | ✅ Accepted on vEthernet adapter | **Works** |
| `127.0.0.1:PORT` (loopback only) | ❌ Rejected — vEthernet traffic is not loopback | **`Connection refused`** |
| `localhost:PORT` | ❌ Same as above on many frameworks | **`Connection refused`** |

**If your dev server binds to loopback**, the Test MCP will fail every test with:

```
✗ Failed: Cannot connect to host host.docker.internal:3005 ssl:default
  [Connect call failed ('192.168.65.254', 3005)]
```

This is **not** a Docker network issue, **not** a firewall issue, and **not** a compose file issue. It is your SUT rejecting container traffic because it is only listening on the loopback interface.

---

## The error you will see if you get this wrong

```
==============================================
Running API Functional Tests
==============================================
  Mode: database (api_library)
  ProjexLight API: https://api.projexlight.com
  API Base URL: http://host.docker.internal:3005
  Environment: development

    Test 1: POST /api/workflows - Create a signing workflow with recipients
      ✗ Failed: Cannot connect to host host.docker.internal:3005 ssl:default
                [Connect call failed ('192.168.65.254', 3005)]
    Test 2: GET /api/users/roles - List available roles (admin only)
      ✗ Failed: Cannot connect to host host.docker.internal:3005 ssl:default
                [Connect call failed ('192.168.65.254', 3005)]
    Test 3: ...
      ✗ Failed: Cannot connect to host host.docker.internal:3005 ssl:default
```

If **every** test fails with the same `Connect call failed` error on `host.docker.internal:PORT`, you are in this failure mode. Fix it by:

1. Confirming your SUT is actually running on the host (see [How to verify](#how-to-verify-your-sut-is-bound-correctly) below)
2. Confirming it is bound to `0.0.0.0:PORT` (not `127.0.0.1:PORT`)
3. Restarting it with the right flags from the tables below

---

## Backend frameworks

### 1. Node.js / Express

**Wrong** (binds to loopback only):
```javascript
app.listen(3005, 'localhost');        // ❌ loopback only
app.listen(3005, '127.0.0.1');        // ❌ loopback only
```

**Right** (binds to all interfaces):
```javascript
app.listen(3005);                     // ✅ Node default is 0.0.0.0
app.listen(3005, '0.0.0.0');          // ✅ explicit all-interfaces
app.listen(3005, '0.0.0.0', () => {}) // ✅ with callback
```

**Verify** while the server is running:
```bash
netstat -ano | findstr :3005   # Windows
lsof -i :3005                  # macOS / Linux
```
You should see `0.0.0.0:3005` or `[::]:3005` in the output. If you see `127.0.0.1:3005`, it is bound to loopback — fix the `app.listen()` call.

### 2. NestJS

NestJS uses Express under the hood. Same rule applies:

```typescript
// main.ts
async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  await app.listen(3005, '0.0.0.0');   // ✅ explicit
  // or just: await app.listen(3005);  // ✅ Node default
}
```

### 3. Python / Django

**Wrong**:
```bash
python manage.py runserver             # ❌ defaults to 127.0.0.1:8000
python manage.py runserver localhost:8000  # ❌ loopback
```

**Right**:
```bash
python manage.py runserver 0.0.0.0:8000    # ✅ explicit all-interfaces
python manage.py runserver 0:8000          # ✅ 0 is shorthand for 0.0.0.0
```

**Security note**: binding Django dev server to `0.0.0.0` can trigger a `DisallowedHost` error. Add `host.docker.internal` and `localhost` to `ALLOWED_HOSTS` in `settings.py`:

```python
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'host.docker.internal', '*']
```

(Use `['*']` only in dev. In prod, enumerate real hostnames.)

### 4. Python / Flask or FastAPI

**Flask** — wrong:
```bash
flask run                              # ❌ defaults to 127.0.0.1:5000
```

**Flask** — right:
```bash
flask run --host=0.0.0.0 --port=5000   # ✅
```

Or in code:
```python
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)  # ✅
```

**FastAPI / Uvicorn** — wrong:
```bash
uvicorn main:app --reload              # ❌ defaults to 127.0.0.1:8000
```

**FastAPI / Uvicorn** — right:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000   # ✅
```

### 5. Java / Spring Boot

Spring Boot defaults to binding on all interfaces (`0.0.0.0`), so `java -jar app.jar` is **safe by default** for this failure mode.

**But** if someone added `server.address` to `application.properties`, make sure it is not loopback:

```properties
# Wrong
server.address=127.0.0.1                # ❌
server.address=localhost                # ❌

# Right (or remove the line entirely — default is 0.0.0.0)
server.address=0.0.0.0                  # ✅
server.port=8080
```

### 6. Go / Gin / Echo / Fiber / net/http

**Wrong**:
```go
http.ListenAndServe("localhost:8080", nil)    // ❌ loopback only
http.ListenAndServe("127.0.0.1:8080", nil)    // ❌ loopback only
```

**Right**:
```go
http.ListenAndServe(":8080", nil)             // ✅ all interfaces (Go default for ":port")
http.ListenAndServe("0.0.0.0:8080", nil)      // ✅ explicit
```

Same for Gin (`r.Run(":8080")`), Echo (`e.Start(":8080")`), Fiber (`app.Listen(":8080")`). Using `:PORT` without a host prefix means all interfaces.

### 7. Ruby on Rails

**Wrong**:
```bash
rails server                           # ❌ from Rails 6.1+, defaults to 127.0.0.1
rails server -b localhost              # ❌ loopback
```

**Right**:
```bash
rails server -b 0.0.0.0                # ✅ all interfaces
rails server -b 0.0.0.0 -p 3000        # ✅ with port
```

Or in `config/puma.rb`:
```ruby
bind "tcp://0.0.0.0:3000"
```

---

## Frontend frameworks

### 8. Vite (React, Vue, Svelte, SolidJS)

**Wrong** (Vite's default is loopback — this is the most common footgun):
```bash
npm run dev                            # ❌ Vite defaults to 127.0.0.1
```

**Right**:
```bash
npm run dev -- --host                  # ✅ exposes on all interfaces
npm run dev -- --host 0.0.0.0          # ✅ explicit
```

Or in `vite.config.ts`:
```typescript
export default defineConfig({
  server: {
    host: '0.0.0.0',   // or true, which is equivalent
    port: 5173,
  },
});
```

**Also required**: Vite rejects requests whose `Host` header it does not recognize. Add `host.docker.internal` to `allowedHosts`:

```typescript
export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['host.docker.internal', 'localhost', '127.0.0.1'],
  },
});
```

Without `allowedHosts`, you will see a different error — requests reach Vite but get rejected with `Blocked request. This host is not allowed.` in the response body.

### 9. Create React App (webpack dev server)

**Wrong**:
```bash
npm start                              # ❌ CRA defaults to 127.0.0.1 since v5
```

**Right**:
```bash
HOST=0.0.0.0 npm start                 # ✅ macOS / Linux
set HOST=0.0.0.0 && npm start          # ✅ Windows cmd
$env:HOST="0.0.0.0"; npm start         # ✅ Windows PowerShell
```

Or in a `.env` file at the project root:
```
HOST=0.0.0.0
DANGEROUSLY_DISABLE_HOST_CHECK=true
```

(The `DANGEROUSLY_DISABLE_HOST_CHECK=true` line is the CRA equivalent of Vite's `allowedHosts` — it lets webpack-dev-server accept requests from `host.docker.internal`.)

### 10. Next.js

**Wrong**:
```bash
next dev                               # ❌ Next.js 13+ defaults to 127.0.0.1
npm run dev                            # ❌ (if it calls `next dev`)
```

**Right**:
```bash
next dev -H 0.0.0.0                    # ✅
next dev --hostname 0.0.0.0            # ✅
next dev -H 0.0.0.0 -p 3000            # ✅ with port
```

Or in `package.json`:
```json
{
  "scripts": {
    "dev": "next dev -H 0.0.0.0 -p 3000"
  }
}
```

### 11. Angular

**Wrong**:
```bash
ng serve                               # ❌ defaults to 127.0.0.1
```

**Right**:
```bash
ng serve --host 0.0.0.0                # ✅
ng serve --host 0.0.0.0 --port 4200    # ✅ with port
```

**Also required**: Angular's dev server blocks non-loopback Host headers by default. Add the `--disable-host-check` flag OR configure it in `angular.json`:

```bash
ng serve --host 0.0.0.0 --disable-host-check   # ✅ quick fix
```

Or permanently in `angular.json` under `architect.serve.options`:
```json
"serve": {
  "options": {
    "host": "0.0.0.0",
    "disableHostCheck": true
  }
}
```

### 12. Nuxt.js / SvelteKit / Astro

**Nuxt** — wrong: `nuxt dev` (defaults to loopback since Nuxt 3).
**Nuxt** — right:
```bash
nuxt dev --host 0.0.0.0
# or in nuxt.config.ts:
#   devServer: { host: '0.0.0.0', port: 3000 }
```

**SvelteKit** — wrong: `vite dev` (it is Vite under the hood).
**SvelteKit** — right:
```bash
vite dev --host              # ✅
# same --host flag, same allowedHosts requirement as standalone Vite
```

**Astro** — wrong: `astro dev`.
**Astro** — right:
```bash
astro dev --host             # ✅
# or in astro.config.mjs:
#   server: { host: '0.0.0.0', port: 4321 }
```

---

## How to verify your SUT is bound correctly

After starting your SUT, run one of these commands on your **host machine** (not inside a container):

**Windows (PowerShell or Git Bash)**:
```bash
netstat -ano | findstr :3005
```

**macOS / Linux**:
```bash
lsof -nP -iTCP:3005 -sTCP:LISTEN
# or
ss -tlnp | grep :3005
```

Read the output's **Local Address** column:

| You see | Meaning | Container → host traffic |
|---|---|---|
| `0.0.0.0:3005` | Bound to **all** IPv4 interfaces | ✅ Will work |
| `[::]:3005` | Bound to **all** IPv6 interfaces (dual-stack) | ✅ Will work |
| `127.0.0.1:3005` | Bound to **loopback only** | ❌ Will fail — restart your SUT with the right flag |
| `[::1]:3005` | Bound to **IPv6 loopback only** | ❌ Same as above |
| **nothing** (empty result) | Your SUT is **not running** | ❌ Start it |

If the address is wrong, stop your dev server and restart it using the correct flag from the tables above.

---

## How to verify the MCP container can reach your SUT

Once the SUT is bound correctly, confirm the Test MCP container can actually reach it:

```bash
docker exec projexlight-test-mcp curl -I --max-time 3 http://host.docker.internal:3005
```

Expected output:
```
HTTP/1.1 200 OK
...
```

Or any non-empty HTTP response (`200`, `302`, `404`, `405` — all are fine, they all prove the TCP connection succeeded).

If you see:
```
curl: (7) Failed to connect to host.docker.internal port 3005
```
...then the SUT is **not reachable** from inside the container. Re-check:
1. Is the SUT actually running? (`netstat` / `lsof` above)
2. Is it bound to `0.0.0.0`? (`netstat` / `lsof` above)
3. Is Windows Firewall blocking the port? (see next section)

---

## Windows Firewall

On Windows, even if your SUT is bound to `0.0.0.0`, the **Windows Defender Firewall** may still block inbound connections from the Docker Desktop virtual adapter. This shows up as a **timeout** (instead of a fast `Connection refused`) when you try to reach the SUT from inside a container.

If you see timeouts specifically on Windows, open PowerShell **as Administrator** and add an allow rule:

```powershell
New-NetFirewallRule `
  -DisplayName "Dev SUT (Docker Desktop)" `
  -Direction Inbound `
  -LocalPort 3005 `
  -Protocol TCP `
  -Action Allow `
  -Profile Private
```

Replace `3005` with your actual port. Repeat for each port your SUT listens on (e.g., `3000` for the frontend, `3005` for the API).

---

## Troubleshooting flowchart

```
┌─────────────────────────────────────────────────┐
│  Test fails with:                               │
│  "Cannot connect to host host.docker.internal"  │
└─────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│  Step 1: Is your SUT running on the host?       │
│  netstat -ano | findstr :PORT   (Windows)       │
│  lsof -iTCP:PORT -sTCP:LISTEN   (macOS/Linux)   │
└─────────────────────────────────────────────────┘
          │                          │
       NO, empty                  YES, found
          │                          │
          ▼                          ▼
┌───────────────────┐   ┌─────────────────────────────┐
│  Start your SUT.  │   │  Step 2: What's the         │
│  Re-run the test. │   │  Local Address column?      │
└───────────────────┘   └─────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
           0.0.0.0:PORT        127.0.0.1:PORT      [::]:PORT
           or [::]:PORT                                  │
                │                   │                   │
                ▼                   ▼                   ▼
    ┌─────────────────────┐  ┌───────────────┐  ┌────────────────┐
    │  Step 3: Try        │  │  Restart SUT  │  │  Same as       │
    │  docker exec ...    │  │  with correct │  │  0.0.0.0 —     │
    │  curl host.docker.  │  │  --host flag  │  │  should work.  │
    │  internal:PORT      │  │  (see tables  │  │  Go to Step 3. │
    └─────────────────────┘  │  above)       │  └────────────────┘
              │              └───────────────┘
              │
     ┌────────┴────────┐
     │                 │
HTTP response      timeout or
received           connection
     │             refused
     │                 │
     ▼                 ▼
┌──────────┐   ┌─────────────────────────────┐
│  You're  │   │  Step 4: Windows only —      │
│  done.   │   │  add firewall rule for the   │
│  Run     │   │  port from Docker vEthernet  │
│  tests.  │   │  (see Windows Firewall       │
└──────────┘   │  section above).             │
               │                              │
               │  Also confirm the port       │
               │  matches BASE_URL/           │
               │  API_BASE_URL in your        │
               │  tests/config/test-config.   │
               │  json.                       │
               └──────────────────────────────┘
```

---

## Need more help?

- **Quick diagnostic**: run `./mcp-server/check-sut.sh` — a helper script that runs all four verification steps automatically and prints actionable output.
- **Test MCP docs**: see `TEST_MCP_FUNCTIONAL_TESTING.md`, section *Troubleshooting → Host Binding*.
- **Dev MCP pre-push hook fails with the same error**: the same rules apply. See the pre-push hook output — it will print this guide's path.
- **Container logs**: `docker logs projexlight-test-mcp -f` and `docker logs projexlight-dev-mcp -f` include network diagnostic info at startup.
