import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SolarSync — Malaysia Rooftop Solar Potential Mapper",
  description:
    "City-scale rooftop solar potential, feasibility analysis and ranking for Selangor, Malaysia.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}

