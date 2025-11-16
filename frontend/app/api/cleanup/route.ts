import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    
    // Proxy cleanup request to backend
    const response = await fetch(`${backendUrl}/api/cleanup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    if (!response.ok) {
      return NextResponse.json(
        { error: "Cleanup failed" },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error calling cleanup endpoint:", error);
    return NextResponse.json(
      { error: "Cleanup request failed" },
      { status: 500 }
    );
  }
}

