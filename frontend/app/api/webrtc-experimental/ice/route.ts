import { NextRequest, NextResponse } from "next/server";

const resolveWebrtcUrl = () =>
  process.env.NEXT_PUBLIC_WEBRTC_API_URL ||
  process.env.WEBRTC_API_URL ||
  "http://localhost:8100";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  try {
    const payload = await req.json();
    const response = await fetch(`${resolveWebrtcUrl()}/api/webrtc-experimental/ice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      return NextResponse.json(
        { error: "Failed to submit ICE candidates" },
        { status: response.status },
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("WebRTC ICE proxy failed:", error);
    return NextResponse.json({ error: "WebRTC ICE proxy failed" }, { status: 500 });
  }
}

