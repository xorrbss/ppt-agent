const WebSocket = require('ws');
const chokidar = require('chokidar');
const path = require('path');

const PORT = 3001;
const LAYOUTS_DIR = path.join(process.cwd(), 'presentation-layouts');

console.log('🔍 Starting Layout Watcher...');
console.log('📁 Watching directory:', LAYOUTS_DIR);

// Create WebSocket server (without path since ws doesn't handle paths like HTTP)
const wss = new WebSocket.Server({ 
    port: PORT
});

console.log(`🔌 WebSocket server running on ws://localhost:${PORT}`);

// Track connected clients
let connectedClients = new Set();

wss.on('connection', (ws) => {
    console.log('👋 Client connected to layout watcher');
    connectedClients.add(ws);

    ws.on('close', () => {
        console.log('👋 Client disconnected from layout watcher');
        connectedClients.delete(ws);
    });

    ws.on('error', (error) => {
        console.error('❌ WebSocket error:', error);
        connectedClients.delete(ws);
    });

    // Send initial connection confirmation
    ws.send(JSON.stringify({
        type: 'connected',
        message: 'Layout watcher connected',
        timestamp: Date.now()
    }));
});

// File watcher setup
const watcher = chokidar.watch(LAYOUTS_DIR, {
    ignored: [
        /(^|[\/\\])\../, // ignore dotfiles
        /node_modules/,
        /\.git/,
        /\.next/,
        /\.DS_Store/,
        /thumbs\.db/i
    ],
    persistent: true,
    ignoreInitial: true, // Don't fire events for initial scan
    followSymlinks: false,
    depth: 3, // Limit depth to avoid deep nested directories
    awaitWriteFinish: {
        stabilityThreshold: 100, // Wait 100ms after last write
        pollInterval: 50
    }
});

// Debounce function to prevent rapid fire events
const debounce = (func, wait) => {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
};

// Broadcast changes to all connected clients
const broadcastChange = debounce((eventType, filePath) => {
    if (connectedClients.size === 0) return;

    const relativePath = path.relative(LAYOUTS_DIR, filePath);
    const pathParts = relativePath.split(path.sep);
    
    // Only process .tsx and .ts files in group directories
    if (pathParts.length >= 2 && (filePath.endsWith('.tsx') || filePath.endsWith('.ts'))) {
        const groupName = pathParts[0];
        const fileName = pathParts[pathParts.length - 1];
        
        // Skip non-layout files
        if (fileName.startsWith('.') || fileName.includes('.test.') || fileName.includes('.spec.')) {
            return;
        }

        const changeData = {
            type: 'layout-change',
            eventType,
            file: relativePath,
            groupName,
            fileName,
            fullPath: filePath,
            timestamp: Date.now()
        };

        console.log(`🔥 Broadcasting ${eventType}:`, relativePath);

        // Send to all connected clients
        connectedClients.forEach(ws => {
            if (ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send(JSON.stringify(changeData));
                } catch (error) {
                    console.error('Error sending WebSocket message:', error);
                    connectedClients.delete(ws);
                }
            } else {
                // Remove disconnected clients
                connectedClients.delete(ws);
            }
        });
    }
}, 200); // Debounce for 200ms

// Watch for file changes
watcher
    .on('change', (filePath) => {
        console.log('📝 File changed:', path.relative(LAYOUTS_DIR, filePath));
        broadcastChange('change', filePath);
    })
    .on('add', (filePath) => {
        console.log('➕ File added:', path.relative(LAYOUTS_DIR, filePath));
        broadcastChange('add', filePath);
    })
    .on('unlink', (filePath) => {
        console.log('🗑️ File removed:', path.relative(LAYOUTS_DIR, filePath));
        broadcastChange('unlink', filePath);
    })
    .on('addDir', (dirPath) => {
        console.log('📁 Directory added:', path.relative(LAYOUTS_DIR, dirPath));
        broadcastChange('addDir', dirPath);
    })
    .on('unlinkDir', (dirPath) => {
        console.log('📁 Directory removed:', path.relative(LAYOUTS_DIR, dirPath));
        broadcastChange('unlinkDir', dirPath);
    })
    .on('error', (error) => {
        console.error('❌ Watcher error:', error);
    })
    .on('ready', () => {
        console.log('✅ Initial scan complete. Ready for changes.');
        console.log(`📊 Watching ${Object.keys(watcher.getWatched()).length} directories`);
    });

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\n🛑 Shutting down layout watcher...');
    watcher.close().then(() => {
        console.log('📝 File watcher closed');
        wss.close(() => {
            console.log('🔌 WebSocket server closed');
            process.exit(0);
        });
    });
});

process.on('SIGTERM', () => {
    console.log('\n🛑 Received SIGTERM, shutting down...');
    watcher.close().then(() => {
        wss.close(() => {
            process.exit(0);
        });
    });
});

// Keep the process alive
console.log('🚀 Layout watcher is running. Press Ctrl+C to stop.'); 