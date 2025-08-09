import { NextResponse } from 'next/server'
import { promises as fs } from 'fs'
import path from 'path'
import { GroupSetting } from '@/app/(presentation-generator)/template-preview/types'

export async function GET() {
  try {
    const layoutsDirectory = path.join(process.cwd(), 'presentation-layouts')
    const items = await fs.readdir(layoutsDirectory, { withFileTypes: true })

    const groupDirectories = items.filter(item => item.isDirectory()).map(dir => dir.name)

    const allLayouts: { groupName: string; files: string[]; settings: GroupSetting | null }[] = []

    for (const groupName of groupDirectories) {
      try {
        const groupPath = path.join(layoutsDirectory, groupName)
        const groupFiles = await fs.readdir(groupPath)

        const layoutFiles = groupFiles.filter(file =>
          file.endsWith('.tsx') &&
          !file.startsWith('.') &&
          !file.includes('.test.') &&
          !file.includes('.spec.') &&
          file !== 'settings.json'
        )

        let settings: GroupSetting | null = null
        const settingsPath = path.join(groupPath, 'settings.json')
        try {
          const settingsContent = await fs.readFile(settingsPath, 'utf-8')
          settings = JSON.parse(settingsContent) as GroupSetting
        } catch {
          settings = {
            description: `${groupName} presentation templates`,
            ordered: false,
            default: false,
          }
        }

        if (layoutFiles.length > 0) {
          allLayouts.push({ groupName, files: layoutFiles, settings })
        }
      } catch (error) {
        console.error(`Error reading group directory ${groupName}:`, error)
      }
    }

    return NextResponse.json(allLayouts)
  } catch (error) {
    console.error('Error reading presentation-layouts directory:', error)
    return NextResponse.json(
      { error: 'Failed to read presentation layouts directory' },
      { status: 500 }
    )
  }
} 