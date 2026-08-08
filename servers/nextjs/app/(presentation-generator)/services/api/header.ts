export const getHeader = () => {
  return {
    "Content-Type": "application/json",
    Accept: "application/json",  
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
};

export const getHeaderForFormData = () => {
  return {
    Accept: "application/json",
  };
};
