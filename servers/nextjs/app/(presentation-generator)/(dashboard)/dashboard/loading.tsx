import { Skeleton } from '@/components/ui/skeleton'
import React from 'react'

const loading = () => {
  return (
    <div className=''>


      <div className='container mx-auto px-4 py-8'>


        <div className=" mx-auto pb-10 grid xl:grid-cols-4 grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4  ">
          {
            Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-72 w-full bg-gray-300 aspect-video mx-auto" />
            ))
          }
        </div>
      </div>

    </div>
  )
}

export default loading
