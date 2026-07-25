"use client";
import React, { useState } from 'react'
import { AlertTriangle, Check, Copy, Trash } from 'lucide-react'
import { Theme } from '@/app/(presentation-generator)/services/api/types'
import ToolTip from '@/components/ToolTip'
import Image from 'next/image'

interface ThemeCardProps {
  theme: Theme
  onSelect: (theme: Theme) => void
  onDelete: (themeId: string) => void
  showDeleteButton?: boolean
}

export const ThemeCard: React.FC<ThemeCardProps> = ({ theme, onSelect, onDelete, showDeleteButton = true }) => {
  if (!theme.data.colors['graph_0']) { return null }
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [copied, setCopied] = useState(false)




  return (<div
    className={` group rounded-xl border w-[305px] cursor-pointer transition-all relative bg-white border-[#EDEEEF]   hover:shadow-md`}
    onClick={() => onSelect(theme)}

  >
    {showDeleteButton && <button
      className="absolute hidden group-hover:block duration-300 transition-all -top-3 -right-3 z-10 bg-white rounded-full p-2  border border-[#EDEEEF] hover:bg-gray-100 hover:text-gray-700"
      style={{ boxShadow: '0 6.6px 13.2px 0 rgba(0, 0, 0, 0.10)' }}
      onClick={(e) => {
        e.stopPropagation()
        setShowDeleteDialog(true)
      }}
    >
      <Trash className="h-3 w-3" />
    </button>}

    {/* Delete Confirmation Dialog */}
    {showDeleteDialog && (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeIn_150ms_ease-out]"
        onClick={(e) => {
          e.stopPropagation()
          setShowDeleteDialog(false)
        }}
      >
        <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" />
        <div
          className="relative bg-white rounded-2xl w-[340px] shadow-2xl animate-[scaleIn_200ms_ease-out] "
          onClick={(e) => e.stopPropagation()}
        >
          <div className="p-6 pb-4 flex flex-col items-center text-center">
            <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center mb-4">
              <AlertTriangle className="h-6 w-6 text-red-500" />
            </div>
            <h3 className="text-lg font-semibold text-[#191919] mb-2">테마를 삭제할까요?</h3>
            <p className="text-sm text-gray-500 leading-relaxed">
              <span className="font-medium text-gray-700">"{theme.name}"</span> 테마를 삭제하려고 합니다. 이 작업은 되돌릴 수 없습니다.
            </p>
          </div>
          <div className="flex border-t border-gray-100">
            <button
              onClick={() => setShowDeleteDialog(false)}
              className="flex-1 px-4 py-3.5 text-sm font-medium text-gray-600 hover:bg-gray-50 transition-colors"
            >
              취소
            </button>
            <button
              onClick={() => {
                onDelete(theme.id)
                setShowDeleteDialog(false)
              }}
              className="flex-1 px-4 py-3.5 text-sm font-medium text-red-500 hover:bg-red-50 transition-colors border-l border-gray-100"
            >
              삭제
            </button>
          </div>
        </div>
      </div>
    )}


    <div className='relative h-[250px] flex justify-center items-center '>

      <Image src="/card_bg.svg" alt="" width={302} height={250} className="absolute top-0 z-[1] left-0 w-[99%] h-full object-cover" />
      <div className=" absolute top-0 left-0 flex items-center justify-between gap-2  z-[2] p-2">
        <ToolTip content='폰트' >

          <p className=" text-xs font-syne  flex gap-1 capitalize  items-center  rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  z-40 ">

            {theme.data.fonts.textFont.name}
          </p>
        </ToolTip>
        {theme.company_name && <ToolTip content='회사'>

          <p className=" text-xs font-syne  flex gap-1 capitalize  items-center  rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  text-ellipsis overflow-hidden whitespace-nowrap z-40 ">

            {theme.company_name}
          </p>
        </ToolTip>}
        {theme.logo_url && <ToolTip content='로고'>

          <p className=" text-xs font-syne  flex gap-1 capitalize  items-center  rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  z-40 ">

            {/* eslint-disable-next-line @next/next/no-img-element -- Theme logos may use arbitrary cross-origin URLs not accepted by the Next image loader. */}
            <img src={theme.logo_url} alt={theme.name} className="w-full max-w-6 h-4 rounded-full object-cover" />
          </p>
        </ToolTip>}



      </div>
      <div className=" relative z-[3] px-6">

        <div className="w-full h-[135px]">
          <div
            className=" w-full h-full rounded-xl p-3 border border-black/10 "
            style={{ backgroundColor: theme.data.colors['background'] }}
          >
            <div
              className="h-[calc(100%-2px)] w-[calc(100%-2px)] mx-auto my-auto rounded-xl p-4 border border-black/10 shadow-[0_2px_6px_rgba(0,0,0,0.10)]"
              style={{ backgroundColor: theme.data.colors['card'] }}
            >
              <div className="h-full w-full flex flex-col justify-center">
                <div
                  className="text-[22px] font-semibold leading-[1.05] text-left truncate"
                  style={{ color: theme.data.colors['background_text'], fontFamily: `"${theme.data.fonts.textFont.name}", ui-serif, Georgia, serif` }}
                >
                  {theme.name}
                </div>
                <div
                  className="mt-1 text-base font-medium leading-[1.1]  text-left truncate"
                  style={{ color: theme.data.colors['background_text'], fontFamily: `"${theme.data.fonts.textFont.name}", ui-serif, Georgia, serif` }}
                >
                  원하는 스타일을 선택하세요.
                </div>
                <div
                  className="mt-2 h-2.5 w-16 rounded-full"
                  style={{ backgroundColor: theme.data.colors['primary'] }}
                />
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>


    <div className='px-5 border-t rounded-b-xl border-[#EDEEEF] w-full py-2.5 h-[80px] bg-white flex items-center justify-between'>
      <div>

        <h4 className='text-sm font-semibold text-[#191919] pb-1'>{theme.name}</h4>
        <div className='flex items-center gap-1'>

          <div className='w-4 h-4 rounded-full border border-[#EDEEEF] '
            style={{ backgroundColor: theme.data.colors['primary'] }}
          />
          <div
            className='w-4 h-4 rounded-full border border-[#EDEEEF]   '
            style={{ backgroundColor: theme.data.colors['background'] }}
          />
        </div>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation()
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(theme.id)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }
        }}
        className={copied ? "text-green-500" : "text-gray-500 hover:text-gray-700"}
        title={copied ? "복사됨!" : "ID 복사"}
      >
        {copied ? <Check className="h-5 w-5" /> : <Copy className="h-5 w-5" />}
      </button>

    </div>
  </div>)

}



