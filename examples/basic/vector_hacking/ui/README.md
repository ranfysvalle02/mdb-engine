# Vector Hacking UI

Static frontend - pure HTML/CSS/JavaScript, no build step required.

## Running

**With the full stack (recommended):**
```bash
cd ..
docker-compose up
```

**Standalone (for development):**
```bash
# Edit config.js to set your API URL
python -m http.server 3000
```

## Configuration

Edit `config.js` to configure the API endpoint:

```javascript
window.VECTOR_HACKING_CONFIG = {
    API_URL: 'http://localhost:8000',
    POLL_INTERVAL: 1000,
    DEBUG: false,
};
```

## Files

| File | Description |
|------|-------------|
| `index.html` | Complete UI (HTML + CSS + JS) |
| `config.js` | API URL configuration |
| `nginx.conf` | Nginx server config |

## Hosting

This UI can be hosted anywhere static files are served:
- AWS S3 + CloudFront
- Netlify / Vercel
- GitHub Pages
- Any web server

Just update `config.js` with your API URL.

## Building the Docker Image

```bash
docker build -t vector-hacking-ui .
```
