/** @type {import('next').NextConfig} */
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  output: "standalone",
  // Same-origin proxy so the app can be shared through a single tunnel:
  // the browser only ever talks to the Next.js origin.
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendOrigin}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
