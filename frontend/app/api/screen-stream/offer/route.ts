import { NextRequest, NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/config";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const backendUrl = getBackendUrl();
    
    const response = await fetch(`${backendUrl}/api/screen-stream/offer`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to proxy screen stream offer:", error);
    return NextResponse.json(
      { error: "Failed to process offer" },
      { status: 500 }
    );
  }
}

