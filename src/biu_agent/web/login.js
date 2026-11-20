const by = (id) => document.getElementById(id);
const register = location.pathname === "/register";
const username = by("username");
const password = by("password");
const confirmation = by("confirm-password");
const message = by("auth-error");
const submit = by("submit");
const nextUsername = "biu.just-registered";

if (register) {
  document.title = "注册账号 · biu-agent";
  by("login-title").textContent = "注册账号";
  by("login-info").textContent = "设置用户名和至少 8 位的密码。注册完成后即可登录。";
  by("confirm-field").hidden = false;
  confirmation.disabled = false;
  confirmation.required = true;
  password.autocomplete = "new-password";
  submit.textContent = "注册";
  by("mode-switch").textContent = "返回登录";
} else {
  const registeredName = sessionStorage.getItem(nextUsername);
  if (registeredName) {
    sessionStorage.removeItem(nextUsername);
    username.value = registeredName;
    by("login-info").textContent = "注册成功，请输入密码登录。";
  }
}
(username.value ? password : username).focus();
by("mode-switch").onclick = () => location.assign(register ? "/login" : "/register");

by("reveal").onclick = () => {
  const show = password.type === "password";
  password.type = show ? "text" : "password";
  by("reveal").textContent = show ? "隐藏" : "显示";
  by("reveal").setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
};

by("login").onsubmit = async (event) => {
  event.preventDefault();
  message.textContent = "";
  if (register && password.value !== confirmation.value) {
    message.textContent = "两次输入的密码不一致。";
    confirmation.focus();
    return;
  }
  submit.disabled = true;
  try {
    const response = await fetch(register ? "/auth/register" : "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    });
    if (!response.ok) {
      const problem = await response.json();
      throw Error(problem.error?.message || "请求失败，请重试。");
    }
    password.value = "";
    confirmation.value = "";
    if (register) sessionStorage.setItem(nextUsername, username.value.trim());
    location.replace(register ? "/login" : "/assistant/chat/");
  } catch (error) {
    message.textContent =
      error instanceof TypeError ? "暂时无法连接工作台，请稍后再试。" : error.message;
  } finally {
    submit.disabled = false;
  }
};
