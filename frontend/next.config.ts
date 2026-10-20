import type { NextConfig } from "next";

// Backend ที่ Next proxy ไปให้ เมื่อ NEXT_PUBLIC_API_BASE=/api (เช่นเปิดผ่าน ngrok tunnel เดียว)
const BACKEND_ORIGIN = (process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // ให้ dev server รับ request ที่มาจากโดเมน tunnel ได้
  allowedDevOrigins: ["*.ngrok-free.app", "*.ngrok-free.dev", "*.ngrok.app", "*.ngrok.dev"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
