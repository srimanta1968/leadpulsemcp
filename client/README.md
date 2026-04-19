# Leadpulse MCP - Frontend

## Technology Stack

- **Framework**: react
- **Language**: typescript
- **Styling**: tailwind

## Getting Started

### Prerequisites

- Node.js 18+ (LTS recommended)
- npm, yarn, or pnpm

### Installation

```bash
# Install dependencies
npm install

# Copy environment file
cp .env.example .env

# Start development server
npm run dev
```

The app will be running at http://localhost:5173

## Available Commands

| Command | Description |
|---------|-------------|
| `npm install` | Install dependencies |
| `npm run dev` | Start development server |
| `npm run build` | Build for production |
| `npm run test` | Run tests |

## Project Structure

See the folder structure for detailed organization based on react conventions.

## Environment Variables

Environment variables use the `VITE_` prefix.

Copy `.env.example` to `.env` and configure:
- API URL for backend connection
- Feature flags
- Third-party service keys (if needed)

## License

MIT
