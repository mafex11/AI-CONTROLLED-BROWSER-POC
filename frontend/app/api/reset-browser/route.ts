import { NextRequest, NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/config";

export async function POST(request: NextRequest) {
  try {
    const backendUrl = getBackendUrl();
    const response = await fetch(`${backendUrl}/api/reset-browser`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: await request.text(),
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to reset browser:", error);
    return NextResponse.json(
      { error: "Failed to reset browser" },
      { status: 500 },
    );
  }
}


