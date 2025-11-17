import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    // Use NEXT_PUBLIC_BACKEND_URL if available, otherwise default
    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8000";
    
    // Proxy request to backend with timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout
    
    const response = await fetch(`${backendUrl}/api/webrtc/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
    
    clearTimeout(timeoutId);

    if (!response.ok) {
      return NextResponse.json(
        { error: "Failed to start WebRTC session" },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error calling WebRTC start endpoint:", error);
    return NextResponse.json(
      { error: "WebRTC start request failed" },
      { status: 500 }
    );
  }
}

