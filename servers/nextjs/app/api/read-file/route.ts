import { NextResponse } from 'next/server';
import fs from 'fs/promises';
import path from 'path';

export async function POST(request: Request) {
  try {
    const { filePath } = await request.json();

    // Validate file path
    if (!filePath || typeof filePath !== 'string') {
      return NextResponse.json(
        { error: 'Invalid file path' },
        { status: 400 }
      );
    }

    // Security check: ensure the path is within /tmp directory
    const normalizedPath = path.normalize(filePath);
    if (!normalizedPath.startsWith('/tmp/')) {
      return NextResponse.json(
        { error: 'Access denied: File must be in /tmp directory' },
        { status: 403 }
      );
    }

    // Read file content
    const content = await fs.readFile(normalizedPath, 'utf-8');
    
    return NextResponse.json({ content });
  } catch (error) {
    console.error('Error reading file:', error);
    return NextResponse.json(
      { error: 'Failed to read file' },
      { status: 500 }
    );
  }
} 