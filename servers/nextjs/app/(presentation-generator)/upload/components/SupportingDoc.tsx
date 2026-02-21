'use client'

import React, { useRef, useState } from 'react'
import { File, X, Upload, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface FileWithId extends File {
    id: string;
}

interface SupportingDocProps {
    files: File[];
    onFilesChange: (files: File[]) => void;
}

const SupportingDoc = ({ files, onFilesChange }: SupportingDocProps) => {
    const [isDragging, setIsDragging] = useState(false)
    const fileInputRef = useRef<HTMLInputElement>(null)

    // Convert Files to FileWithId with proper type checking
    const filesWithIds: FileWithId[] = files.map(file => {
        const fileWithId = file as FileWithId
        fileWithId.id = `${file.name || 'unnamed'}-${file.lastModified || Date.now()}-${file.size || 0}`
        return fileWithId
    })

    const formatFileSize = (bytes: number): string => {
        if (!bytes || bytes === 0) return '0 Bytes'
        const k = 1024
        const sizes = ['Bytes', 'KB', 'MB', 'GB']
        const i = Math.floor(Math.log(bytes) / Math.log(k))
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
    }

    const handleDragEvents = (e: React.DragEvent<HTMLDivElement>, isDragging: boolean) => {
        e.preventDefault()
        e.stopPropagation()
        setIsDragging(isDragging)
    }

    const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault()
        e.stopPropagation()
        setIsDragging(false)

        const droppedFiles = Array.from(e.dataTransfer.files);
        const hasPdf = files.some(file => file.type === 'application/pdf');

        const validTypes = [
            'application/pdf',
            'text/plain',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        ];

        const invalidFiles = droppedFiles.filter(file => !validTypes.includes(file.type));
        if (invalidFiles.length > 0) {
            toast.error('Invalid file type', {
                description: 'Please upload only PDF, TXT, PPTX, or DOCX files',
            });
            return;
        }

        if (hasPdf && droppedFiles.some(file => file.type === 'application/pdf')) {
            toast.error('Multiple PDF files are not allowed', {
                description: 'Please select only one PDF file',
            });
            return;
        }

        const validFiles = droppedFiles.filter(file => {
            return !(hasPdf && file.type === 'application/pdf');
        });

        if (validFiles.length > 0) {
            const updatedFiles = [...files, ...validFiles]
            onFilesChange(updatedFiles)

            toast.success('Files selected', {
                description: `${validFiles.length} file(s) have been added`,
            })
        }
    }

    const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFiles = Array.from(e.target.files || []);

        const hasPdf = files.some(file => file.type === 'application/pdf');

        const validFiles = selectedFiles.filter(file => {
            return !(hasPdf && file.type === 'application/pdf');
        });

        if (validFiles.length > 0) {
            const updatedFiles = [...files, ...validFiles]
            onFilesChange(updatedFiles)

            toast.success('Files selected', {
                description: `${validFiles.length} file(s) have been added`,
            })
        }
    }

    const removeFile = (fileId: string) => {
        const updatedFiles = files.filter(file => {
            const currentFileId = `${file.name || 'unnamed'}-${file.lastModified || Date.now()}-${file.size || 0}`
            return currentFileId !== fileId
        })
        onFilesChange(updatedFiles)
    }


    return (
        <div className="w-full bg-[#F6F6F9]  px-2.5 py-3.5 rounded-[10px]  ">

            <div
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                    "w-full border cursor-pointer border-dashed border-[#B8B8C1] rounded-lg",
                    "transition-all duration-300 ease-in-out ",
                    " flex flex-col ",
                    isDragging && "border-purple-400 bg-purple-50"
                )}
                onDragOver={(e) => handleDragEvents(e, true)}
                onDragLeave={(e) => handleDragEvents(e, false)}
                onDrop={handleDrop}
            >
                <div className="flex-1 flex  gap-2 items-center justify-center p-6">
                    <div className='w-[42px] h-[42px] flex justify-center items-center rounded-full bg-[#EBE9FE]' >
                        <div className='w-[22px] h-[22px] rounded-full bg-[#7A5AF8] flex items-center justify-center text-white'>
                            <Plus className='w-3 h-3' />
                        </div>
                    </div>
                    <div>

                        <p className=" text-xs text-[#808080]  ">
                            {isDragging
                                ? <span className=' '>Drop your file here</span>
                                : <span className=' '> <span className=' underline underline-offset-4'>Click to Upload</span> or drag &amp; drop.</span>
                            }
                        </p>
                        <p className="text-gray-400 text-sm text-center mt-1 ">
                            Supports PDFs, Text files, PPTX, DOCX
                        </p>
                    </div>

                    <input
                        type="file"
                        accept=".pdf,.txt,.pptx,.docx"
                        onChange={handleFileInput}
                        className="hidden"
                        id="file-upload"
                        ref={fileInputRef}
                        multiple
                        data-testid="file-upload-input"
                    />

                    {/* <button
                        onClick={(e) => {
                            e.stopPropagation()
                            fileInputRef.current?.click()
                        }}
                        className="px-6 py-2 bg-purple-600 text-white rounded-full
                            hover:bg-purple-700 transition-colors duration-200
                            font-medium text-sm"
                    >
                        Choose Files
                    </button> */}
                </div>

                {files.length > 0 && (
                    <div className="border-t border-gray-200 bg-gray-50 rounded-b-lg">
                        <div className="p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-sm font-medium text-gray-700">
                                    Selected Files ({files.length})
                                </h3>
                            </div>
                            <div data-testid="file-list" className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                                {filesWithIds.map((file) => {

                                    return (
                                        (
                                            <div key={file.id}
                                                className="bg-white rounded-lg border border-gray-200 overflow-hidden
                                            hover:border-purple-200 group relative"
                                            >
                                                <div className="p-4 bg-purple-50 group-hover:bg-purple-100 
                                            transition-colors flex items-center justify-center relative"
                                                >

                                                    <File className="w-8 h-8 text-purple-600" />

                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation()
                                                            removeFile(file.id)
                                                        }}
                                                        className="absolute top-1 right-2 p-1.5
                                                    bg-white/80 backdrop-blur-sm rounded-full
                                                    text-gray-500 hover:text-red-500 
                                                    shadow-sm hover:shadow-md
                                                    transition-all duration-200"
                                                        aria-label="Remove file"
                                                    >
                                                        <X className="w-4 h-4" />
                                                    </button>
                                                </div>

                                                <div className="p-3 relative">
                                                    <p className="text-sm font-medium text-gray-700 truncate mb-1 pr-2">
                                                        {file.name || 'Unnamed File'}
                                                    </p>
                                                    <p className="text-xs text-gray-500">
                                                        {formatFileSize(file.size)}
                                                    </p>
                                                </div>
                                            </div>
                                        )
                                    )
                                })}
                            </div>
                        </div>
                    </div>
                )}

            </div>
        </div>
    )
}

export default SupportingDoc
