# The Delight Pulse

Weekly executive dashboard digests for delight.ai leadership.

## Deploy to Vercel

### Option 1: Vercel CLI
```bash
npm i -g vercel
cd delight-pulse
vercel
```

### Option 2: GitHub → Vercel
1. Push this repo to GitHub
2. Import it at [vercel.com/new](https://vercel.com/new)
3. Vercel will auto-detect the static config — just click Deploy

### Adding new digests
1. Drop the new `.html` file into `/public`
2. Add a new `<li class="digest-item">` entry to `public/index.html`
3. Push / redeploy

## Structure
```
delight-pulse/
├── vercel.json
├── README.md
└── public/
    ├── index.html                                    ← Archive landing page
    ├── Weekly_Dashboard_Digest_-_Feb_23__2026.html   ← Week 3
    └── Weekly_Dashboard_Digest_-_Mar_2__2026.html    ← Week 5
```
