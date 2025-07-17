import { NextResponse } from 'next/server'
import { promises as fs } from 'fs'
import path from 'path'

export async function GET() {
    try {
        // Get the path to the presentation-layouts directory
        const layoutsDirectory = path.join(process.cwd(), 'presentation-layouts')
        
        // Read all directories in the presentation-layouts directory
        const items = await fs.readdir(layoutsDirectory, { withFileTypes: true })
        
        // Filter for directories (layout groups) and exclude files
        const groupDirectories = items
            .filter(item => item.isDirectory())
            .map(dir => dir.name)
        
        const allLayouts: { group: string; files: string[] }[] = []
        
        // Scan each group directory for layout files
        for (const groupName of groupDirectories) {
            try {
                const groupPath = path.join(layoutsDirectory, groupName)
                const groupFiles = await fs.readdir(groupPath)
                
                // Filter for .tsx files and exclude any non-layout files
                const layoutFiles = groupFiles.filter(file => 
                    file.endsWith('.tsx') && 
                    !file.startsWith('.') && 
                    !file.includes('.test.') &&
                    !file.includes('.spec.') &&
                    file !== 'setting.json'
                )
                
                if (layoutFiles.length > 0) {
                    allLayouts.push({
                        group: groupName,
                        files: layoutFiles
                    })
                }
            } catch (error) {
                console.error(`Error reading group directory ${groupName}:`, error)
                // Continue with other groups even if one fails
            }
        }
        
        return NextResponse.json(allLayouts)
    } catch (error) {
        console.error('Error reading presentation-layouts directory:', error)
        return NextResponse.json(
            { error: 'Failed to read presentation-layouts directory' },
            { status: 500 }
        )
    }
} 