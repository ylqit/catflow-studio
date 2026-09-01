import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { api } from "./api/client";
import { router } from "./router";
import "./styles.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);

api.bootstrap().finally(() => app.mount("#app"));
