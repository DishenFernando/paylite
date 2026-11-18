"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let token = sessionStorage.getItem("paylite_token") || "";
let currentUser = null;
let pendingTransfer = null;
const asRs = (number) => `Rs. ${Number(number).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2})}`;

function showToast(message, isError=false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => box.classList.add("hidden"), 4600);
}

async function api(path, options={}) {
  const headers = {"Content-Type":"application/json", ...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try { response = await fetch(path, {...options, headers}); }
  catch { throw new Error("Network unavailable. Please check the server and retry."); }
  if (options.download && response.ok) return response.blob();
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.endsWith("/login") && token) {
      token = ""; sessionStorage.removeItem("paylite_token"); showAuth();
    }
    let detail = data.detail || `Request failed (${response.status})`;
    if (Array.isArray(detail)) detail = detail.map(d => d.msg).join("; ");
    throw new Error(detail);
  }
  return data;
}

function withBusy(form, fn) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  return Promise.resolve().then(fn).catch(e => showToast(e.message, true)).finally(() => {button.disabled = false;});
}

function showAuth() {
  $("#auth-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
  $("#logout").classList.add("hidden");
  currentUser = null;
}
function showApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#logout").classList.remove("hidden");
  $("#admin-nav").classList.toggle("hidden", currentUser.role !== "ADMIN");
  $("#welcome").textContent = `Hello, ${currentUser.name.split(" ")[0]} 👋`;
  $("#account-name").textContent = currentUser.name;
  $("#account-mobile").textContent = currentUser.mobile;
  $("#avatar").textContent = currentUser.name[0].toUpperCase();
  setTab("home");
}
function setTab(tab) {
  if (tab === "admin" && currentUser?.role !== "ADMIN") return;
  $$(".tab").forEach(el => el.classList.toggle("hidden", el.id !== `tab-${tab}`));
  $$(".nav-button").forEach(el => el.classList.toggle("active", el.dataset.tab === tab));
  if (tab === "statement") loadStatement().catch(e => showToast(e.message,true));
  if (tab === "admin") loadAdmin().catch(e => showToast(e.message,true));
  if (tab === "send") loadUsers().catch(e => showToast(e.message,true));
}
async function refreshWallet() {
  const result = await api("/api/wallet");
  currentUser = result.user;
  $("#balance").textContent = asRs(result.balance);
  $("#remaining").textContent = asRs(result.remaining_limit);
  $("#sent-today").textContent = `${asRs(result.sent_today)} used`;
  $("#daily-limit").textContent = `of ${asRs(result.daily_limit)}`;
  $("#limit-progress").style.width = `${Math.min(100, Number(result.sent_today) / Number(result.daily_limit) * 100)}%`;
  return result;
}
async function loadUsers() {
  const users = await api("/api/users");
  const list = $("#recipients");
  list.replaceChildren();
  users.forEach(user => {const option=document.createElement("option"); option.value=user.mobile; option.label=user.name; list.appendChild(option);});
}
function row(cells) {
  const tr=document.createElement("tr");
  cells.forEach(cell=>{const td=document.createElement("td");
    if (cell instanceof Node) td.appendChild(cell);
    else td.textContent=cell ?? "—";
    tr.appendChild(td);
  }); return tr;
}
function element(tag,text,cls) {const el=document.createElement(tag); el.textContent=text; if(cls) el.className=cls; return el;}
function filterQuery() {
  const form = $("#statement-form");
  const from = form.elements.from_date.value;
  const to = form.elements.to_date.value;
  if (from && to && from > to) throw new Error("From date must be before To date");
  const q = new URLSearchParams();
  if (from) q.set("from_date", from);
  if (to) q.set("to_date", to);
  return q.toString();
}
async function loadStatement() {
  const qs=filterQuery();
  const entries=await api(`/api/statement?${qs}`);
  const body=$("#statement-rows"); body.replaceChildren();
  entries.forEach(entry=>{
    const name=entry.type === "TOPUP" ? "Wallet top-up" : `${entry.type[0]}${entry.type.slice(1).toLowerCase()}`;
    const status=element("div", name); status.appendChild(element("small", ` · ${entry.reference}`));
    const amount=element("strong", `${entry.direction === "IN" ? "+" : "−"}${asRs(entry.amount)}`, entry.direction === "IN" ? "credit" : "debit");
    body.appendChild(row([status,entry.counterparty ? `${entry.counterparty} (${entry.counterparty_mobile})` : "—",new Date(entry.at).toLocaleString(),amount,asRs(entry.balance_after)]));
  });
  $("#statement-empty").classList.toggle("hidden",entries.length>0);
}
async function loadAdmin() {
  const entries=await api("/api/admin/transfers");
  const body=$("#admin-rows"); body.replaceChildren();
  entries.forEach(entry=>{
    const status=element("span",entry.status,`status-pill ${entry.status==="REVERSED"?"reversed":""}`);
    let action=element("span","Completed");
    if(entry.status==="COMPLETED") {
      action=element("button","Reverse","small-button"); action.type="button";
      action.addEventListener("click",async()=>{
        if(!confirm(`Reverse ${asRs(entry.amount)} from ${entry.recipient} back to ${entry.sender}?`))return;
        action.disabled=true;
        try {await api(`/api/admin/transfers/${entry.id}/reverse`,{method:"POST"});showToast("Transfer reversed successfully.");await Promise.all([loadAdmin(),refreshWallet()]);}
        catch(e){showToast(e.message,true);action.disabled=false;}
      });
    }
    body.appendChild(row([entry.id,`${entry.sender} → ${entry.recipient}`,asRs(entry.amount),status,action]));
  });
  $("#admin-empty").classList.toggle("hidden",entries.length>0);
}

$("#show-login").addEventListener("click",()=>{
  $("#login-form").classList.remove("hidden");$("#register-form").classList.add("hidden");
  $("#show-login").classList.add("selected");$("#show-register").classList.remove("selected");
});
$("#show-register").addEventListener("click",()=>{
  $("#login-form").classList.add("hidden");$("#register-form").classList.remove("hidden");
  $("#show-login").classList.remove("selected");$("#show-register").classList.add("selected");
});
$$(".demo-user").forEach(b=>b.addEventListener("click",()=>{
  const form=$("#login-form");form.elements.mobile.value=b.dataset.mobile;
  form.elements.password.value=b.dataset.password;
  showToast("Demo details filled. Click Log in to continue.");
}));
$("#login-form").addEventListener("submit", e=>{e.preventDefault();const form=e.currentTarget;
  withBusy(form,async()=>{
    const data=await api("/api/auth/login",{method:"POST",body:JSON.stringify({mobile:form.elements.mobile.value,password:form.elements.password.value})});
    token=data.token;sessionStorage.setItem("paylite_token",token);currentUser=data.user;
    showApp();await refreshWallet();showToast("Welcome back!");
  });
});
$("#register-form").addEventListener("submit",e=>{e.preventDefault();const form=e.currentTarget;
  withBusy(form,async()=>{
    const data=Object.fromEntries(new FormData(form));
    await api("/api/auth/register",{method:"POST",body:JSON.stringify(data)});
    $("#show-login").click();$("#login-form").elements.mobile.value=data.mobile;
    $("#login-form").elements.password.value=data.password;
    form.reset();showToast("Account created! Log in with your new wallet.");
  });
});
$("#logout").addEventListener("click",async()=>{
  try{await api("/api/auth/logout",{method:"POST"});}catch{}
  token="";sessionStorage.removeItem("paylite_token");showAuth();showToast("You have logged out.");
});
$$(".nav-button").forEach(b=>b.addEventListener("click",()=>setTab(b.dataset.tab)));
$$("[data-goto]").forEach(b=>b.addEventListener("click",()=>setTab(b.dataset.goto)));
$$("[data-amount]").forEach(b=>b.addEventListener("click",()=>{$('#topup-form').elements.amount.value=b.dataset.amount;}));
$("#topup-form").addEventListener("submit",e=>{e.preventDefault();const form=e.currentTarget;
  withBusy(form,async()=>{
    await api("/api/wallet/topup",{method:"POST",body:JSON.stringify({amount:form.elements.amount.value})});
    form.reset();await refreshWallet();showToast("Money added successfully!");setTab("home");
  });
});
$("#transfer-form").addEventListener("input",()=>{pendingTransfer=null;});
$("#transfer-form").addEventListener("submit",e=>{e.preventDefault();const form=e.currentTarget;
  withBusy(form,async()=>{
    const payload={recipient_mobile:form.elements.recipient_mobile.value,amount:form.elements.amount.value,pin:form.elements.pin.value};
    const signature=JSON.stringify(payload);
    if(!pendingTransfer || pendingTransfer.signature!==signature)pendingTransfer={signature,key:crypto.randomUUID()};
    const result=await api("/api/transfers",{method:"POST",headers:{"Idempotency-Key":pendingTransfer.key},body:signature});
    pendingTransfer=null;form.reset();await refreshWallet();
    showToast(result.replayed?"Transfer already processed. No duplicate charge.":`Successfully sent ${asRs(result.amount)} to ${result.recipient}!`);
    setTab("statement");
  });
});
$("#statement-form").addEventListener("submit",e=>{e.preventDefault();loadStatement().catch(e=>showToast(e.message,true));});
$("#export-csv").addEventListener("click",async()=>{
  try{const blob=await api(`/api/statement/export?${filterQuery()}`,{download:true});
    const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="paylite-statement.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),500);
  }catch(e){showToast(e.message,true);}
});
$("#check-integrity").addEventListener("click",async()=>{
  try{const data=await api("/api/wallet/integrity");
    $("#integrity-output").textContent=data.matches?`✓ Verified: wallet and ledger both equal ${asRs(data.wallet_balance)}.`:"⚠ Wallet and ledger balances do not match!";
  }catch(e){showToast(e.message,true);}
});
(async()=>{
  if(!token){showAuth();return;}
  try{await refreshWallet();showApp();}
  catch{token="";sessionStorage.removeItem("paylite_token");showAuth();}
})();
