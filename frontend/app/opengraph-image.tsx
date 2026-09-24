import { ImageResponse } from "next/og";
import { ShareCard, brandFonts } from "@/lib/brand";

export const runtime = "edge";
export const alt = "BetIQ — AI Football Predictions";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(<ShareCard />, { ...size, fonts: await brandFonts() });
}
