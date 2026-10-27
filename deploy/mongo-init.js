// Dedicated Prism app user; initialization runs only on a fresh dedicated volume.
const password = process.env.PRISM_MONGO_APP_PASSWORD;
if (!password) throw new Error("PRISM_MONGO_APP_PASSWORD is required");
const prism = db.getSiblingDB("lumenx_prism");
prism.createUser({user: "prism_app", pwd: password,
                  roles: [{role: "readWrite", db: "lumenx_prism"}]});
