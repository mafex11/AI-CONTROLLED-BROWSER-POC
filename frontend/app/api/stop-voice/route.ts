import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  try {
    const response = await fetch("http://localhost:8000/api/stop-voice", {
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
    console.error("Failed to stop voice:", error);
    return NextResponse.json(
      { error: "Failed to stop voice" },
      { status: 500 },
    );
  }
}


