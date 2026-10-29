'use strict';
const message={credentials:'Email, username or password did not match. Please try again.',exists:'An account already uses this email. Sign in instead.',invalid:'Check the form fields and use a password with 12–128 characters.'}[new URLSearchParams(location.search).get('error')];
const target=document.getElementById('form-error');if(message&&target){target.textContent=message;target.hidden=false;}
