import { NextRequest, NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/config";

export async function GET(request: NextRequest) {
  try {
    const backendUrl = getBackendUrl();
    const response = await fetch(`${backendUrl}/api/screen-stream/ice-servers`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to fetch ICE servers:", error);
    return NextResponse.json(
      { error: "Failed to fetch ICE servers" },
      { status: 500 }
    );
  }
}

