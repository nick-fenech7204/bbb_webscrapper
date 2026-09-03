# Deploying to AWS (S3 + CloudFront)

A step-by-step console walkthrough -- no AWS CLI needed. Written to teach
the pieces, not just get you deployed: read the "why" notes, don't just
click through.

**The shape of it:** S3 holds the actual files (private -- nothing reaches
it directly). CloudFront is a CDN that fetches from S3 and serves the
world, over HTTPS, from edge locations close to whoever's visiting. This is
*the* standard pattern for a static site on AWS -- you're not learning a
workaround, this is how real production static sites are actually deployed.

**Cost:** for a site this size (a couple MB of data, low traffic), this
should run pennies a month, likely $0 if you're within your first 12 months
on the AWS account (S3 and CloudFront both have meaningful free tiers). You
pay for storage + requests + data transfer, all usage-based -- no fixed
monthly fee for the infrastructure itself.

---

## Part 1 -- Create the S3 bucket

1. Sign in to the [AWS Console](https://console.aws.amazon.com), go to **S3**.
2. **Create bucket**.
3. **Bucket name**: must be globally unique across *all* AWS accounts, not
   just yours -- e.g. `<yourname>-bbb-insight-site`. Lowercase, no spaces.
4. **Region**: any is fine -- `us-east-1` (N. Virginia) is a reasonable
   default and is required later anyway if you add a custom domain (ACM
   certificates for CloudFront must be requested in us-east-1 specifically).
5. **Block Public Access settings**: leave **all four boxes checked**
   (the default). This might look wrong for a "public site," but it's
   correct -- the bucket itself should stay completely private. CloudFront
   will be the *only* thing allowed to read from it (Part 3 sets that up).
   Nothing should ever be able to hit the bucket directly.
6. Leave everything else default, **Create bucket**.

## Part 2 -- Upload the site files

1. Open your new bucket, click **Upload**.
2. Click **Add folder** and select this repo's `site/` folder -- or drag its
   *contents* (`index.html`, `css/`, `js/`, `data/`) in directly. Either
   way, the important thing is the bucket ends up with `index.html` at its
   root, and `css/style.css`, `js/app.js`, `data/manifest.json`, etc. at
   those same relative paths (not nested inside an extra `site/` folder).
3. **Upload**. Once it finishes, click into the bucket and confirm you see
   `index.html`, `css/`, `js/`, `data/` at the top level.

(**Don't** upload `DEPLOY.md` or `README.md` -- harmless if you do, just
clutter. Not a security issue, they're already public in the git repo.)

## Part 3 -- Create the CloudFront distribution

1. Go to **CloudFront** in the console, **Create distribution**.
2. **Origin domain**: click the field, your S3 bucket should appear in the
   suggestions -- select it. (If it shows the bucket's *website endpoint*
   instead of its regular one, don't use that -- keep typing/searching for
   the plain bucket name; the regular endpoint is what pairs with OAC below.)
3. **Origin access**: choose **Origin access control settings
   (recommended)**. Click **Create new OAC**, accept the defaults, **Create**.
   This is the "only CloudFront can read this bucket" mechanism -- an IAM-style
   permission CloudFront presents when it fetches from S3, that a plain
   public visitor hitting S3 directly could never present.
4. After creating the OAC, CloudFront will show a **yellow banner** saying
   the S3 bucket policy needs updating, with a **Copy policy** button. Click
   it (copies a JSON policy to your clipboard) -- you'll paste this in Part 4.
5. **Default root object**: type `index.html`. Without this, visiting your
   site's bare domain (no path) won't know to serve `index.html` -- you'd
   get an error instead of the homepage.
6. **Viewer protocol policy**: **Redirect HTTP to HTTPS** (forces encrypted
   connections -- standard practice, no reason not to).
7. **Price class**: "Use all edge locations" is the default (best global
   performance, standard cost). If you want to shave cost and don't care
   about fast loads outside North America/Europe, "Use only North America
   and Europe" is cheaper -- optional, either is fine to start.
8. Leave the rest default, **Create distribution**. It'll show status
   **Deploying** -- this takes a few minutes to propagate to edge locations
   worldwide. Grab a coffee.

## Part 4 -- Update the S3 bucket policy

1. Go back to your **S3 bucket** -> **Permissions** tab -> **Bucket policy**
   -> **Edit**.
2. **Paste** the policy CloudFront gave you to copy in Part 3, step 4. It'll
   look something like this (don't hand-type it -- use the one CloudFront
   generated, it has your exact bucket name and distribution ID filled in):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "AllowCloudFrontServicePrincipal",
         "Effect": "Allow",
         "Principal": { "Service": "cloudfront.amazonaws.com" },
         "Action": "s3:GetObject",
         "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*",
         "Condition": {
           "StringEquals": { "AWS:SourceArn": "arn:aws:cloudfront::YOUR-ACCOUNT-ID:distribution/YOUR-DISTRIBUTION-ID" }
         }
       }
     ]
   }
   ```
   (If you missed the copy-policy banner, you can regenerate it: open the
   distribution in CloudFront -> **Origins** tab -> select your origin ->
   **Edit** -> the same banner/copy-policy option reappears.)
3. **Save changes**.

## Part 5 -- Test it

1. Back in CloudFront, wait until your distribution's status is
   **Deployed** (refresh the page; usually 3-10 minutes).
2. Click into the distribution, copy its **Distribution domain name**
   (looks like `d1a2b3c4d5e6f7.cloudfront.net`).
3. Open that URL in a browser -- you should see the live site, metro/industry
   dropdowns populated, everything working exactly like your local preview.

If you get an error instead:
- **403 Forbidden**: almost always the bucket policy (Part 4) -- double
  check it saved, and that the distribution ID in it matches yours.
- **Blank page / dropdowns stuck on "Loading"**: open the browser's dev
  tools (F12) -> Console/Network tab, check for a failed request to
  `data/manifest.json` -- usually means the upload in Part 2 didn't
  preserve the folder structure correctly.

## Updating the site later

Whenever you re-run `scripts/publish_site_data.py` or edit any site file:

1. Re-upload the **changed files** to the S3 bucket (same Upload flow as
   Part 2 -- you can re-upload just the changed ones, or the whole folder
   again, it'll overwrite).
2. **Invalidate the CloudFront cache** so visitors see the update
   immediately instead of a cached old version: in the distribution ->
   **Invalidations** tab -> **Create invalidation** -> path `/*` ->
   **Create invalidation**. Takes a minute or two to clear.

(Once this stops feeling new, the AWS CLI does both of these in two
commands -- `aws s3 sync site/ s3://your-bucket/` and `aws cloudfront
create-invalidation --distribution-id YOUR_ID --paths "/*"` -- worth
switching to once you're comfortable with what those commands are actually
doing under the hood.)

## Optional: a custom domain

Not required -- the `*.cloudfront.net` URL works fine and is a normal HTTPS
address. If you want `yourdomain.com` instead, later:

1. Register a domain (Route 53, or any registrar -- doesn't have to be AWS).
2. Request a certificate for it in **ACM, in the us-east-1 region
   specifically** (a CloudFront requirement, regardless of which region
   your distribution or bucket are in) -- validate it via the DNS record
   ACM gives you.
3. On the CloudFront distribution -> **Settings** -> **Alternate domain
   names (CNAMEs)** -> add your domain, attach the certificate.
4. Point your domain's DNS at the CloudFront distribution -- an A/ALIAS
   record in Route 53, or a CNAME if using another registrar's DNS.

Happy to walk through this part when you're ready for it -- it's a
separate, self-contained step from everything above.
