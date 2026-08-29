"use client";

import React, { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import { File, Paperclip, Plus, X } from 'lucide-react'
import { notify } from '@/components/ui/sonner'
import { useUploadLimits } from '@/lib/use-upload-limits'
import {
    sourceLockedSelectionErrorMessage,
    validateSourceLockedFiles,
} from '../../services/api/source-locked/contract'

interface SupportingDocProps {
    files: File[]
    onFilesChange: (files: File[]) => void
    accept?: string
    multiple?: boolean
    /** Compact variant for the compose card: a paperclip button + file chips instead of the full dropzone. */
    compact?: boolean
    sourceLocked?: boolean
}

export const MAX_SUPPORTED_FILES = 20
export const SUPPORTED_FILE_FORMATS_DESCRIPTION = 'Word, PowerPoint, 스프레드시트, PDF, TXT, Markdown, 이미지 파일.'

const PDF_TYPES = ['.pdf']
export const TEXT_EXTENSIONS = ['.txt', '.md', '.markdown'] as const
export const TEXT_MIME_TYPES = ['text/plain', 'text/markdown'] as const
const WORD_TYPES = ['.doc', '.docx', '.docm', '.odt', '.rtf']
const POWERPOINT_TYPES = ['.ppt', '.pptx', '.pptm', '.odp']
const SPREADSHEET_TYPES = ['.xls', '.xlsx', '.xlsm', '.ods', '.csv', '.tsv']
const IMAGE_TYPES = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg']

const ALLOWED_MIME_PREFIXES: string[] = ['image/']
const ALLOWED_MIME_TYPES = [
    'application/pdf',
    ...TEXT_MIME_TYPES,
    'text/csv',
    'application/csv',
    'text/tab-separated-values',
    'text/tsv',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-word.document.macroenabled.12',
    'application/vnd.oasis.opendocument.text',
    'application/rtf',
    'text/rtf',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.ms-powerpoint.presentation.macroenabled.12',
    'application/vnd.oasis.opendocument.presentation',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel.sheet.macroenabled.12',
    'application/vnd.oasis.opendocument.spreadsheet',
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/bmp',
    'image/tiff',
    'image/webp',
    'image/svg+xml',
]
const ALLOWED_EXTENSIONS = [
    ...PDF_TYPES,
    ...TEXT_EXTENSIONS,
    ...WORD_TYPES,
    ...POWERPOINT_TYPES,
    ...SPREADSHEET_TYPES,
    ...IMAGE_TYPES,
]
const ACCEPT_DEFAULT = [...ALLOWED_MIME_TYPES, ...ALLOWED_EXTENSIONS].join(',')

const SupportingDoc = ({
    files,
    onFilesChange,
    accept = ACCEPT_DEFAULT,
    multiple = true,
    compact = false,
    sourceLocked = false,
}: SupportingDocProps) => {
    const uploadLimits = useUploadLimits()
    const [isDragging, setIsDragging] = useState(false)
    const [previewUrls, setPreviewUrls] = useState<(string | null)[]>([])
    const fileInputRef = useRef<HTMLInputElement>(null)

    const hasFiles = files.length > 0

    const filteredFiles = useMemo(() => {
        return files.filter((file) => isAllowedFile(file) && file.size <= uploadLimits.document.bytes)
    }, [files, uploadLimits.document.bytes])

    useEffect(() => {
        const urls = filteredFiles.map((file) => (file.type.startsWith('image/') ? URL.createObjectURL(file) : null))
        setPreviewUrls(urls)

        return () => {
            urls.forEach((url) => {
                if (url) URL.revokeObjectURL(url)
            })
        }
    }, [filteredFiles])

    const handleValidate = (filesToReview: File[]) => {
        const disallowed = filesToReview.filter((file) => !isAllowedFile(file))
        if (disallowed.length > 0) {
            notify.error('지원하지 않는 파일이 있습니다', `지원 형식: ${SUPPORTED_FILE_FORMATS_DESCRIPTION}`)
        }
        const oversized = filesToReview.filter((file) => file.size > uploadLimits.document.bytes)
        if (oversized.length > 0) {
            notify.error(
                '파일이 너무 큽니다',
                `파일당 최대 크기는 ${uploadLimits.document.mb}MB입니다.`
            )
        }
    }

    const applyFileLimit = (candidateFiles: File[]) => {
        if (candidateFiles.length <= MAX_SUPPORTED_FILES) {
            return candidateFiles
        }

        notify.warning('최대 파일 수 초과', `문서는 최대 ${MAX_SUPPORTED_FILES}개까지만 업로드할 수 있습니다.`)

        return candidateFiles.slice(0, MAX_SUPPORTED_FILES)
    }

    const handleFilesSelected = (e: ChangeEvent<HTMLInputElement>) => {
        const selectedFiles = Array.from(e.target.files ?? [])
        if (selectedFiles.length === 0) return

        const nextFiles = multiple ? [...files, ...selectedFiles] : [selectedFiles[0]]
        const sourceLockedError = sourceLocked ? validateSourceLockedFiles(nextFiles) : null
        if (sourceLockedError) {
            notify.error('원본 문서 그대로 생성 파일 제한', sourceLockedSelectionErrorMessage(sourceLockedError))
            e.currentTarget.value = ''
            return
        }
        const allowedFiles = applyFileLimit(
            nextFiles.filter((file) => isAllowedFile(file) && file.size <= uploadLimits.document.bytes)
        )

        onFilesChange(allowedFiles)
        handleValidate(nextFiles)
        if (allowedFiles.length > files.length) {
            notify.success('파일 선택 완료', `${allowedFiles.length - files.length}개의 파일이 추가되었습니다.`)
        }
        e.currentTarget.value = ''
    }

    const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault()
        setIsDragging(false)

        const droppedFiles = Array.from(e.dataTransfer.files ?? [])
        if (droppedFiles.length === 0) return

        const nextFiles = multiple ? [...files, ...droppedFiles] : [droppedFiles[0]]
        const sourceLockedError = sourceLocked ? validateSourceLockedFiles(nextFiles) : null
        if (sourceLockedError) {
            notify.error('원본 문서 그대로 생성 파일 제한', sourceLockedSelectionErrorMessage(sourceLockedError))
            return
        }
        const allowedFiles = applyFileLimit(
            nextFiles.filter((file) => isAllowedFile(file) && file.size <= uploadLimits.document.bytes)
        )

        onFilesChange(allowedFiles)
        handleValidate(nextFiles)
        if (allowedFiles.length > files.length) {
            notify.success('파일 선택 완료', `${allowedFiles.length - files.length}개의 파일이 추가되었습니다.`)
        }
    }

    const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault()
        setIsDragging(true)
    }

    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault()
        setIsDragging(false)
    }

    const handleRemoveFileAt = (index: number) => {
        const nextFiles = filteredFiles.filter((_, i) => i !== index)
        onFilesChange(nextFiles)
    }

    const handleClearFiles = () => {
        if (!hasFiles) return
        onFilesChange([])
    }

    const openFilePicker = () => fileInputRef.current?.click()

    const handleDropzoneKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            openFilePicker()
        }
    }

    return (
        <div className="space-y-2" data-testid="attachments-uploader">
            {compact ? (
                <div className="flex items-center gap-3">
                    <div
                        className="relative inline-flex h-[34px] items-center gap-2 overflow-hidden rounded-full px-3.5 text-xs font-medium text-[#191919] font-syne shadow-sm ring-1 ring-inset ring-slate-200 transition-colors hover:ring-[#5146E5]/40 focus-within:ring-2 focus-within:ring-[#5146E5] focus-within:ring-offset-2"
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            className="absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
                            onChange={handleFilesSelected}
                            accept={accept}
                            multiple={multiple}
                            data-testid="file-upload-input"
                            aria-label="첨부 파일 선택"
                        />
                        <Paperclip aria-hidden="true" className="w-3.5 h-3.5" />
                        <span aria-hidden="true">{hasFiles ? `첨부 ${filteredFiles.length}개` : '파일 첨부'}</span>
                    </div>
                    {hasFiles && (
                        <button
                            type="button"
                            onClick={handleClearFiles}
                            className="text-xs font-medium text-red-600 hover:text-red-700 font-syne"
                            data-testid="attachments-clear-button"
                        >
                            모두 지우기
                        </button>
                    )}
                </div>
            ) : (
                <>
                    <div className="flex items-center justify-between">
                        <p className="text-sm text-gray-600 font-syne">
                            {hasFiles ? `첨부파일 ${filteredFiles.length}개` : ''}
                        </p>
                        {hasFiles && <button
                            type="button"
                            onClick={handleClearFiles}
                            disabled={!hasFiles}
                            className={`text-sm font-medium font-syne ${!hasFiles ? 'cursor-not-allowed text-gray-400' : 'text-red-600 hover:text-red-700'}`}
                            data-testid="attachments-clear-button"
                            aria-disabled={!hasFiles}
                        >
                            모두 지우기
                        </button>}
                    </div>

                    <div
                        role="button"
                        tabIndex={0}
                        aria-label="첨부 파일 선택 또는 끌어 놓기"
                        className={`mt-1 block cursor-pointer rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5146E5] focus-visible:ring-offset-2 ${isDragging ? 'border-[#5146E5] bg-[#5146E5]/5' : 'border-gray-200 hover:border-[#5146E5]'}`}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        onClick={openFilePicker}
                        onKeyDown={handleDropzoneKeyDown}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            className="sr-only"
                            onChange={handleFilesSelected}
                            accept={accept}
                            multiple={multiple}
                            data-testid="file-upload-input"
                            tabIndex={-1}
                            aria-hidden="true"
                        />
                        <div className="flex flex-col items-center gap-2">
                            <div className='w-[42px] h-[42px] flex justify-center items-center rounded-full bg-[#EBE9FE]' >
                                <div className='w-[22px] h-[22px] rounded-full bg-[#7A5AF8] flex items-center justify-center text-white'>
                                    <Plus className='w-3 h-3' />
                                </div>
                            </div>
                            <p className='text-[#808080] text-sm  font-normal'>(Office 문서, 스프레드시트, 이미지, PDF, TXT, Markdown)</p>
                            <p className='text-[#808080] text-xs font-normal'>
                                파일당 최대 {uploadLimits.document.mb}MB
                            </p>
                        </div>
                    </div>
                </>
            )}

            {hasFiles && (
                <div className="mt-2">
                    <ul data-testid="file-list" className={`grid grid-cols-1 gap-2 ${compact ? '' : 'sm:grid-cols-2'}`} aria-label="첨부된 파일">
                        {filteredFiles.map((file, idx) => (
                            <li
                                key={`${file.name}-${idx}`}
                                className="flex items-center gap-3 rounded-md border border-gray-200 px-3 py-2"
                                data-testid="attached-file-item"
                            >
                                {previewUrls[idx] ? (
                                    // eslint-disable-next-line @next/next/no-img-element -- File previews are browser-created blob URLs and cannot use the Next image optimizer.
                                    <img src={previewUrls[idx] as string} alt="미리보기" className="h-10 w-10 flex-none rounded object-cover" />
                                ) : (
                                    <div className="flex h-10 w-10 flex-none items-center justify-center rounded bg-gray-100 text-gray-600">
                                        <File className="h-5 w-5" />
                                    </div>
                                )}

                                <div className="min-w-0 flex-1">
                                    <p className="truncate text-sm font-medium text-gray-900 font-syne" title={file.name}>
                                        {file.name}
                                    </p>
                                    <p className="text-xs text-gray-500 font-syne">{formatFileSize(file.size)}</p>
                                </div>

                                <button
                                    type="button"
                                    onClick={() => handleRemoveFileAt(idx)}
                                    className="ml-2 inline-flex h-8 w-8 items-center justify-center rounded text-red-600 hover:bg-red-50 hover:text-red-700"
                                    aria-label={`${file.name} 삭제`}
                                    data-testid="remove-file-button"
                                >
                                    <X className="h-5 w-5" />
                                </button>
                            </li>
                        ))}
                    </ul>
                    {filteredFiles.length !== files.length && (
                        <p className="mt-2 text-xs text-amber-600 font-syne">
                            일부 파일이 제외되었습니다. 지원 형식: {SUPPORTED_FILE_FORMATS_DESCRIPTION}
                        </p>
                    )}
                </div>
            )}
        </div>
    )
}

const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes <= 0) return '0 KB'
    return `${(bytes / 1024).toFixed(1)} KB`
}

export function isAllowedFile(file: File): boolean {
    const type = (file.type || '').toLowerCase()
    const name = (file.name || '').toLowerCase()
    const typeAllowed = ALLOWED_MIME_TYPES.includes(type) || ALLOWED_MIME_PREFIXES.some((prefix) => type.startsWith(prefix))
    const extension = name.includes('.') ? `.${name.split('.').pop()}` : ''

    // Browsers often report Markdown as text/plain, application/octet-stream,
    // or an empty MIME type. For named files, the allow-listed extension is
    // authoritative so an arbitrary extension cannot bypass validation by
    // claiming a benign MIME type.
    if (extension) return ALLOWED_EXTENSIONS.includes(extension)
    return typeAllowed
}

export default SupportingDoc
